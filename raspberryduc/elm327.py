"""ELM327 serial interpreter: AT command session over a USB VCP.

The bench adapter is an ELM327 v1.5 clone (FT232R) that talks at 38400 first.
The M3C is KWP2000 on 11-bit 500 kbit/s CAN, not passenger-car Mode 01 / UDS
SID 0x22. Multi-frame VIN (KWP 1A90) needs raw ISO-TP: headers on, CAF off,
and an 8-byte flow-control frame if consecutive frames do not already follow
the first frame in the same RX. After ATMA, ATCRA must be cleared (ATAR) or
later diagnostic replies to 7E9 are filtered away.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import serial

LOG = logging.getLogger(__name__)

# v1.5 clones on this bike answered at 38400; 115200 is next for STN/genuine units.
PROBE_BAUDS = (38400, 115200, 9600, 57600, 230400)


class ElmError(RuntimeError):
    pass


class Elm327:
    def __init__(self, timeout: float = 2.0) -> None:
        self.timeout = timeout
        self.port_name: Optional[str] = None
        self.baudrate: Optional[int] = None
        self.adapter_id: str = ""
        self._ser: Optional[serial.Serial] = None

    @property
    def connected(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    def open(self, device: str, baudrate: int) -> None:
        self.close()
        ser = serial.Serial(
            port=device,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=self.timeout,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        # USB-serial bridges often need DTR/RTS asserted before ATZ is accepted.
        try:
            ser.dtr = True
            ser.rts = True
        except Exception:
            pass
        time.sleep(0.4)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        self._ser = ser
        self.port_name = device
        self.baudrate = baudrate
        LOG.debug("Opened %s @ %s", device, baudrate)

    def close(self) -> None:
        if self._ser is not None:
            LOG.debug("Closing %s", self.port_name)
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self.port_name = None
        self.baudrate = None
        self.adapter_id = ""

    def probe_and_open(self, device: str, bauds: tuple = PROBE_BAUDS) -> str:
        last_error = "no response"
        for baud in bauds:
            try:
                self.open(device, baud)
                ident = self.reset()
                if not _looks_like_elm(ident):
                    ident = self.command("ATI", timeout=1.5)
                if _looks_like_elm(ident):
                    self.adapter_id = ident.strip().splitlines()[-1].strip()
                    LOG.info("ELM327 on %s @ %s: %s", device, baud, self.adapter_id)
                    return ident
                self.close()
            except Exception as exc:
                last_error = str(exc)
                self.close()
        raise ElmError(f"No ELM327 on {device}: {last_error}")

    def reset(self) -> str:
        # ATZ is slow (~1 s) and reverts protocol; configure_iso15765 must follow.
        raw = self.command("ATZ", timeout=3.0)
        return raw

    def configure_iso15765(self, protocol: str = "6") -> str:
        """Initialize ISO 15765-4 (CAN). Protocol 6 = 11-bit 500 kbit/s (M3C)."""
        # ATE0/ATL0/ATS0 keep logs parseable. ATH0 until ISO-TP/ATMA need IDs.
        # ATAL allows >7 data bytes. ATCAF1/ATCFC1 are the default diag path;
        # VIN 1A90 temporarily turns CAF off. ATSTFF = longest timeout.
        # ATCPAA: 29-bit tester present page (harmless on 11-bit). ATV1 = variable DLC.
        steps = (
            ("ATE0", 1.0),
            ("ATL0", 1.0),
            ("ATS0", 1.0),
            ("ATH0", 1.0),
            ("ATAL", 1.0),
            ("ATCAF1", 1.0),
            ("ATCFC1", 1.0),
            ("ATSTFF", 1.0),
            ("ATCPAA", 1.0),
            ("ATV1", 1.0),
            ("ATSP" + protocol, 1.5),
        )
        last = ""
        for cmd, timeout in steps:
            last = self.command(cmd, timeout=timeout)
            # Clones ERROR on ATAL/ATCAF/ATCP; keep going unless the session itself failed.
            fatal = cmd.startswith(("ATE", "ATL", "ATS0", "ATH", "ATSP"))
            if fatal and "ERROR" in last.upper() and "BUS" not in last.upper():
                raise ElmError(f"{cmd} failed: {last}")
        return last

    def isotp_request(self, pdu: str, timeout: float = 2.5) -> bytes:
        """Send a 2-byte KWP PDU as a raw ISO-TP single frame (PCI + SID + data).

        CAF0 is required: CAF1 would send ``03 1A 90`` and the M3C treats ``03``
        as Mode 03. ATH1 keeps 7E9 on the wire so first/consecutive frames can
        be assembled. ATV0 pads to 8 bytes for clones that drop short frames.
        """
        pdu = pdu.strip().replace(" ", "").upper()
        pci = f"{len(pdu) // 2:02X}{pdu}"
        collected = bytearray()
        try:
            self.command("ATH1", timeout=0.5)
            self.command("ATCAF0", timeout=0.5)
            self.command("ATV0", timeout=0.4)
            raw = self.command(pci, timeout=timeout)
            collected.extend(_can_data_bytes(raw))
            assembled = assemble_isotp(bytes(collected))
            if _needs_flow_control(collected, assembled):
                # ECU waits for an 8-byte CTS; do not flush RX first (CFs can follow immediately).
                for fc in ("30000000000000", "300000AAAAAAAAAA"):
                    # 30 00 00 = CTS, block size 0 (continue). Padding variants for clones.
                    self._tx(fc)
                    more = self._read_until_prompt(timeout)
                    LOG.debug("RX %s", _one_line(more))
                    collected.extend(_can_data_bytes(more))
                    assembled = assemble_isotp(bytes(collected))
                    if assembled and not _needs_flow_control(collected, assembled):
                        break
                    if more.upper().find("NO DATA") < 0 and _can_data_bytes(more):
                        break
            return assembled or parse_hex_bytes(raw)
        finally:
            # Restore CAF so later AT commands are not treated as raw CAN data.
            try:
                self.command("ATV1", timeout=0.4)
                self.command("ATCAF1", timeout=0.5)
                self.command("ATH0", timeout=0.5)
            except Exception:
                pass

    def _tx(self, cmd: str) -> None:
        """Write without reset_input_buffer — ISO-TP CFs can already be in RX."""
        if not self._ser:
            raise ElmError("Adapter is not open")
        LOG.debug("TX %s", cmd.strip())
        self._ser.write(cmd.strip().encode("ascii", "ignore") + b"\r")
        self._ser.flush()

    def protocol_name(self) -> str:
        return self.command("ATDP", timeout=1.5)

    def command(self, cmd: str, timeout: Optional[float] = None) -> str:
        if not self._ser:
            raise ElmError("Adapter is not open")
        wait = self.timeout if timeout is None else timeout
        payload = cmd.strip().encode("ascii", "ignore") + b"\r"
        LOG.debug("TX %s", cmd.strip())
        # Flush stale ATMA/STOPPED bytes so they are not parsed as this reply.
        self._ser.reset_input_buffer()
        self._ser.write(payload)
        self._ser.flush()
        response = self._read_until_prompt(wait)
        LOG.debug("RX %s", _one_line(response))
        if _monitor_noise(response) and cmd.strip().upper() != "ATAR":
            # ATMA leave-behind: adapter still in monitor until CR + ATAR.
            self._interrupt_monitor()
            self._tx("ATAR")
            LOG.debug("RX %s", _one_line(self._read_until_prompt(0.4)))
            LOG.debug("TX %s", cmd.strip())
            self._ser.reset_input_buffer()
            self._ser.write(payload)
            self._ser.flush()
            response = self._read_until_prompt(wait)
            LOG.debug("RX %s", _one_line(response))
        return response

    def sniff_can(self, can_id: int, timeout: float = 0.4) -> str:
        """ATMA for one 11-bit ID. Must ATAR afterward or ATCRA sticks on that ID.

        With ATH1+CRA, clones often print 8 data bytes and no ID. Collect the
        full window (do not stop on the first CR) then send CR to exit monitor.
        """
        cid = f"{can_id:03X}"
        self._interrupt_monitor()
        self.command("ATH1", timeout=0.4)
        self.command(f"ATCRA{cid}", timeout=0.4)
        if not self._ser:
            raise ElmError("Adapter is not open")
        LOG.debug("TX ATMA (filter %s, %.2fs)", cid, timeout)
        self._ser.reset_input_buffer()
        self._ser.write(b"ATMA\r")
        self._ser.flush()
        deadline = time.time() + timeout
        buf = bytearray()
        self._ser.timeout = 0.05
        while time.time() < deadline:
            chunk = self._ser.read(128)
            if chunk:
                buf.extend(chunk)
        try:
            self._ser.write(b"\r")
            self._ser.flush()
        except Exception:
            pass
        leftover = self._read_until_prompt(0.4)
        try:
            self.command("ATH0", timeout=0.4)
            self.command("ATAR", timeout=0.4)
        except Exception:
            pass
        text = buf.decode("ascii", "replace") + "\n" + leftover
        text = text.replace("\x00", "")
        LOG.debug("RX ATMA %s", _one_line(text))
        return text

    def _interrupt_monitor(self) -> None:
        if not self._ser:
            return
        try:
            self._ser.write(b"\r")
            self._ser.flush()
            self._read_until_prompt(0.3)
        except Exception:
            pass

    def _read_until_prompt(self, timeout: float) -> str:
        assert self._ser is not None
        deadline = time.time() + timeout
        buf = bytearray()
        self._ser.timeout = 0.15
        while time.time() < deadline:
            chunk = self._ser.read(128)
            if chunk:
                buf.extend(chunk)
                if b">" in chunk:
                    break
            elif buf.endswith(b">"):
                break
        text = buf.decode("ascii", "replace")
        text = text.replace("\x00", "")
        # Drop the command echo and prompt.
        lines = []
        for line in text.replace("\r", "\n").split("\n"):
            stripped = line.strip()
            if not stripped or stripped == ">":
                continue
            lines.append(stripped)
        return "\n".join(lines)


def _one_line(text: str, limit: int = 2000) -> str:
    compact = " | ".join(line.strip() for line in text.splitlines() if line.strip())
    if not compact:
        return "(empty)"
    if len(compact) > limit:
        return compact[:limit] + "…"
    return compact


def _looks_like_elm(text: str) -> bool:
    upper = text.upper()
    return any(token in upper for token in ("ELM", "STN11", "STN21", "SCANTOOL", "OBDLINK"))


def parse_hex_bytes(response: str) -> bytes:
    """Hex payload from an ELM reply. Strips 7E8/7E9 IDs so ISO-TP PCI is byte 0."""
    out = bytearray()
    for line in response.replace(",", " ").replace("|", "\n").splitlines():
        upper = line.strip().upper()
        if not upper:
            continue
        if any(
            token in upper
            for token in (
                "SEARCHING",
                "OK",
                "STOPPED",
                "BUS INIT",
                "NO DATA",
                "UNABLE",
                "ERROR",
                "TIMEOUT",
                "CAN ERROR",
                "BUFFER FULL",
                "BUSERROR",
                "FB ERROR",
            )
        ):
            continue
        if ":" in upper:
            upper = upper.split(":", 1)[-1]
        # Clone length-only line such as "013" (ISO-TP first-frame length).
        if len(upper) == 3 and all(ch in "0123456789ABCDEF" for ch in upper):
            continue
        # ATH1 lines start with the 11-bit ID; PCI/SID follow those three hex digits.
        for prefix in ("7E8", "7E9", "7E0", "7E1"):
            if upper.startswith(prefix):
                upper = upper[len(prefix) :].strip()
                break
        cleaned = "".join(ch for ch in upper if ch in "0123456789ABCDEF")
        if len(cleaned) < 2:
            continue
        if len(cleaned) % 2 == 1:
            cleaned = cleaned[:-1]
        try:
            out.extend(bytes.fromhex(cleaned))
        except ValueError:
            continue
    return bytes(out)


def _monitor_noise(response: str) -> bool:
    upper = (response or "").upper()
    return "STOPPED" in upper or ">?" in upper or upper.strip() in {"?", "STOPPED"}


def _can_data_bytes(response: str) -> bytes:
    return parse_hex_bytes(response)


def _needs_flow_control(raw: bytes, assembled: bytes) -> bool:
    """True when we have an ISO-TP first frame (PCI 1x) but not yet `length` bytes."""
    if len(raw) < 2 or raw[0] & 0xF0 != 0x10:
        return False
    length = ((raw[0] & 0x0F) << 8) | raw[1]
    return len(assembled) < length


def assemble_isotp(data: bytes) -> bytes:
    """Single-frame (0x0n) or FF/CF (0x1n / 0x2n). SID 0x5A/0x7F is already a PDU."""
    if not data:
        return b""
    if data[0] == 0x7F or 0x40 <= data[0] <= 0x7E:
        # Positive KWP/UDS SID or NRC already (CAF may have stripped PCI).
        return data
    if data[0] <= 0x07:
        n = data[0]
        return data[1 : 1 + n]
    if data[0] & 0xF0 != 0x10 or len(data) < 2:
        return b""
    length = ((data[0] & 0x0F) << 8) | data[1]
    body = bytearray(data[2 : min(len(data), 8)])
    i = min(len(data), 8)
    while i < len(data) and len(body) < length:
        pci = data[i]
        if pci & 0xF0 != 0x20:
            break
        i += 1
        take = min(7, length - len(body), len(data) - i)
        body.extend(data[i : i + take])
        i += take
    return bytes(body[:length] if len(body) >= length else body)


def response_failed(response: str) -> bool:
    upper = response.upper()
    return any(
        token in upper
        for token in (
            "NO DATA",
            "UNABLE TO CONNECT",
            "CAN ERROR",
            "BUS ERROR",
            "STOPPED",
            "ERROR",
            "TIMEOUT",
            "?",
        )
    )
