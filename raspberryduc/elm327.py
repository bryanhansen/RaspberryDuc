"""ELM327 serial interpreter: AT command session over a USB VCP."""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import serial

LOG = logging.getLogger(__name__)

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

    def close(self) -> None:
        if self._ser is not None:
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
        raw = self.command("ATZ", timeout=3.0)
        return raw

    def configure_iso15765(self, protocol: str = "6") -> str:
        """Initialize ISO 15765-4 (CAN) on the adapter. Protocol 6 = 11-bit 500 kbit/s."""
        steps = (
            ("ATE0", 1.0),
            ("ATL0", 1.0),
            ("ATS0", 1.0),
            ("ATH0", 1.0),
            ("ATSP" + protocol, 1.5),
        )
        last = ""
        for cmd, timeout in steps:
            last = self.command(cmd, timeout=timeout)
            if "ERROR" in last.upper() and "BUS" not in last.upper():
                raise ElmError(f"{cmd} failed: {last}")
        return last

    def protocol_name(self) -> str:
        return self.command("ATDP", timeout=1.5)

    def command(self, cmd: str, timeout: Optional[float] = None) -> str:
        if not self._ser:
            raise ElmError("Adapter is not open")
        wait = self.timeout if timeout is None else timeout
        payload = cmd.strip().encode("ascii", "ignore") + b"\r"
        self._ser.reset_input_buffer()
        self._ser.write(payload)
        self._ser.flush()
        return self._read_until_prompt(wait)

    def sniff_can(self, can_id: int, timeout: float = 0.45) -> str:
        """Capture a short burst of traffic for one 11-bit CAN ID, then restore headers-off."""
        cid = f"{can_id:03X}"
        self.command("ATH1", timeout=0.4)
        self.command(f"ATCRA{cid}", timeout=0.4)
        if not self._ser:
            raise ElmError("Adapter is not open")
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
                if b"\r" in buf and len(buf) > 6:
                    break
        try:
            self._ser.write(b"\r")
            self._ser.flush()
        except Exception:
            pass
        leftover = self._read_until_prompt(0.5)
        try:
            self.command("ATH0", timeout=0.4)
        except Exception:
            pass
        text = buf.decode("ascii", "replace") + "\n" + leftover
        return text.replace("\x00", "")

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


def _looks_like_elm(text: str) -> bool:
    upper = text.upper()
    return any(token in upper for token in ("ELM", "STN11", "STN21", "SCANTOOL", "OBDLINK"))


def parse_hex_bytes(response: str) -> bytes:
    """Extract hex payload from an ELM327 response, ignoring SEARCHING/OK noise."""
    out = bytearray()
    for line in response.replace(",", " ").splitlines():
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
        # Keep only hex pairs / nibbles.
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
