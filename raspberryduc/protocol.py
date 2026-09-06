"""ISO 11898 / ISO 15765-4 session for the Continental M3C ECU."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from raspberryduc.detect import SerialCandidate, list_candidates
from raspberryduc.dtc import FaultCode, decode_mode03, decode_uds_dtc, unique_codes
from raspberryduc.elm327 import Elm327, ElmError, parse_hex_bytes, response_failed

LOG = logging.getLogger(__name__)

# ISO 15765-4 protocol numbers as understood by ELM327 (ISO 11898 CAN physical).
ISO15765_PROTOCOLS: Sequence[Tuple[str, str]] = (
    ("6", "ISO 15765-4 CAN 11-bit 500 kbit/s"),
    ("7", "ISO 15765-4 CAN 29-bit 500 kbit/s"),
    ("8", "ISO 15765-4 CAN 11-bit 250 kbit/s"),
    ("9", "ISO 15765-4 CAN 29-bit 250 kbit/s"),
)

# Physical diagnostic addresses commonly used on motorcycle ECUs including M3C.
PHYS_ADDRS_11BIT = (
    ("7E0", "7E8"),
    ("7E1", "7E9"),
)

VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")


@dataclass
class VehicleSnapshot:
    vin: str = ""
    vin_source: str = ""
    faults: List[FaultCode] = field(default_factory=list)
    protocol: str = ""
    ecu_ok: bool = False
    notes: str = ""


@dataclass
class ConnectionInfo:
    port: str
    baudrate: int
    adapter_id: str
    protocol: str
    description: str = ""


class M3CSession:
    """ELM327 session targeting the 2015 Scrambler Continental M3C."""

    def __init__(self) -> None:
        self.elm = Elm327()
        self.info: Optional[ConnectionInfo] = None
        self.snapshot = VehicleSnapshot()

    @property
    def connected(self) -> bool:
        return self.elm.connected

    def connect(self, preferred_port: Optional[str] = None) -> ConnectionInfo:
        last_error = "No USB ELM327 adapter found"
        candidates: List[SerialCandidate] = list_candidates()
        if preferred_port:
            candidates = [
                SerialCandidate(preferred_port, "user", "", None, None, 100)
            ] + [c for c in candidates if c.device != preferred_port]
        if not candidates:
            raise ElmError(
                "No USB virtual COM port that looks like an ELM327 was found"
            )

        for cand in candidates:
            try:
                ident = self.elm.probe_and_open(cand.device)
                protocol = self._select_protocol()
                self.info = ConnectionInfo(
                    port=cand.device,
                    baudrate=self.elm.baudrate or 0,
                    adapter_id=self.elm.adapter_id or ident.splitlines()[-1],
                    protocol=protocol,
                    description=cand.description,
                )
                self.snapshot = self.read_vehicle()
                return self.info
            except Exception as exc:
                last_error = f"{cand.device}: {exc}"
                LOG.warning("Connect attempt failed: %s", last_error)
                self.elm.close()
                self.info = None
        raise ElmError(last_error)

    def disconnect(self) -> None:
        self.elm.close()
        self.info = None
        self.snapshot = VehicleSnapshot()

    def read_vehicle(self) -> VehicleSnapshot:
        snap = VehicleSnapshot()
        if self.info:
            snap.protocol = self.info.protocol
        vin, source = self._read_vin()
        snap.vin = vin
        snap.vin_source = source
        snap.faults = self._read_faults()
        snap.ecu_ok = bool(vin) or bool(snap.faults) or self._ping_ecu()
        if not snap.ecu_ok:
            snap.notes = "ELM327 is up, but the M3C ECU did not answer. Ignition ON?"
        elif not vin:
            snap.notes = "ECU responded; VIN was not available via 0902 or UDS F190."
        self.snapshot = snap
        return snap

    def _select_protocol(self) -> str:
        """Prefer a live ISO 15765-4 bus; default to 11-bit 500 kbit/s for the M3C."""
        last_error = "protocol select failed"
        fallback = None
        for code, name in ISO15765_PROTOCOLS:
            try:
                self.elm.configure_iso15765(code)
                probe = self.elm.command("0100", timeout=3.0)
                reported = self.elm.protocol_name()
                label = f"{name} [{reported}]"
                upper = probe.upper()
                if "UNABLE TO CONNECT" in upper or "BUS ERROR" in upper:
                    last_error = probe
                    if fallback is None and code == "6":
                        fallback = label
                    continue
                if parse_hex_bytes(probe):
                    LOG.info("Selected live protocol %s", label)
                    return label
                if fallback is None:
                    fallback = label
            except Exception as exc:
                last_error = str(exc)
        if fallback:
            LOG.info("Using fallback ISO 15765-4 protocol %s", fallback)
            return fallback
        raise ElmError(f"ISO 15765-4 not established: {last_error}")

    def _ping_ecu(self) -> bool:
        resp = self.elm.command("0100", timeout=2.5)
        data = parse_hex_bytes(resp)
        return bool(data) and not response_failed(resp)

    def _read_vin(self) -> Tuple[str, str]:
        # ISO 15031-5 / SAE J1979 Mode 09 PID 02 (VIN)
        raw = self.elm.command("0902", timeout=4.0)
        vin = _vin_from_mode09(parse_hex_bytes(raw))
        if vin:
            return vin, "OBD Mode 09 PID 02"
        # ISO 14229 UDS ReadDataByIdentifier 0xF190 over ISO 15765-4
        for tx, rx in PHYS_ADDRS_11BIT:
            try:
                self._address_ecu(tx, rx)
                raw = self.elm.command("22F190", timeout=4.0)
                vin = _vin_from_uds(parse_hex_bytes(raw))
                if vin:
                    return vin, f"UDS DID F190 @{tx}/{rx}"
            except Exception as exc:
                LOG.debug("UDS VIN %s failed: %s", tx, exc)
        return "", ""

    def _read_faults(self) -> List[FaultCode]:
        faults: List[FaultCode] = []
        # Stored / confirmed DTCs (ISO 15031-5 service 03)
        raw = self.elm.command("03", timeout=3.0)
        if not response_failed(raw):
            faults.extend(decode_mode03(parse_hex_bytes(raw)))
        # Pending DTCs
        raw = self.elm.command("07", timeout=3.0)
        if not response_failed(raw):
            faults.extend(decode_mode03(parse_hex_bytes(raw)))
        if faults:
            return unique_codes(faults)
        # UDS ReadDTCInformation reportDTCByStatusMask
        for tx, rx in PHYS_ADDRS_11BIT:
            try:
                self._address_ecu(tx, rx)
                raw = self.elm.command("1902FF", timeout=4.0)
                if response_failed(raw):
                    continue
                decoded = decode_uds_dtc(parse_hex_bytes(raw))
                if decoded:
                    return unique_codes(decoded)
            except Exception as exc:
                LOG.debug("UDS DTC %s failed: %s", tx, exc)
        return unique_codes(faults)

    def _address_ecu(self, tx: str, rx: str) -> None:
        self.elm.command(f"ATSH{tx}", timeout=1.0)
        self.elm.command(f"ATCRA{rx}", timeout=1.0)


def _vin_from_mode09(payload: bytes) -> str:
    if not payload:
        return ""
    data = payload
    if data[:1] == b"\x49":
        data = data[1:]
    if data[:1] == b"\x02":
        data = data[1:]
    # Drop ISO-TP / CAN frame-index bytes (01, 02, 03...) when present.
    ascii_bytes = bytearray()
    for b in data:
        if 32 <= b < 127:
            ascii_bytes.append(b)
    text = bytes(ascii_bytes).decode("ascii", "ignore").replace("\x00", "").strip()
    match = VIN_RE.search(text.replace(" ", ""))
    return match.group(0) if match else ""


def _vin_from_uds(payload: bytes) -> str:
    if not payload:
        return ""
    data = payload
    if data[:1] == b"\x62":
        data = data[1:]
    if len(data) >= 2 and data[0] == 0xF1 and data[1] == 0x90:
        data = data[2:]
    text = "".join(chr(b) for b in data if 32 <= b < 127)
    match = VIN_RE.search(text.replace(" ", ""))
    return match.group(0) if match else ""
