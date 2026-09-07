"""ISO 11898 / ISO 15765-4 session for the 2015 Scrambler Continental M3C.

What the ECU actually speaks (confirmed on a live bike):
- Physical: ISO 15765-4 CAN 11-bit 500 kbit/s (ELM ATSP6). Mode 01 ``0100`` is
  NO DATA; the bus is still up.
- Addressing: tester 7E1 / ECU 7E9 (7E0/7E8 is silent).
- Session: KWP ``1003`` → ``5003``. Three-byte UDS (``22F190``, ``1902FF``)
  returns NRC 0x13 (incorrect message length).
- VIN: KWP ``1A90`` over raw ISO-TP → ``5A 90`` + ASCII
  (example ZDMK100AAFB002933).
- Live data: no Mode 01 PIDs. Broadcast CAN (0x081 TPS, 0x100 RPM, 0x201 volts)
  plus ATRV for adapter voltage.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from raspberryduc.detect import SerialCandidate, list_candidates
from raspberryduc.dtc import FaultCode, decode_mode03, decode_uds_dtc, unique_codes
from raspberryduc.elm327 import Elm327, ElmError, parse_hex_bytes, response_failed
from raspberryduc.live import LIVE_PARAMS, NONE_KEY, can_payload, mode01_payload, parse_atrv
from raspberryduc.service import (
    ServiceSnapshot,
    grips_from_can_280,
    indicator_from_remaining,
    indicators_from_can_201,
    option_from_flag,
    parse_option_flag,
    parse_packed_service,
    parse_remaining,
    OptionState,
)

LOG = logging.getLogger(__name__)

# ISO 15765-4 protocol numbers as understood by ELM327 (ISO 11898 CAN physical).
ISO15765_PROTOCOLS: Sequence[Tuple[str, str]] = (
    ("6", "ISO 15765-4 CAN 11-bit 500 kbit/s"),
    ("7", "ISO 15765-4 CAN 29-bit 500 kbit/s"),
    ("8", "ISO 15765-4 CAN 11-bit 250 kbit/s"),
    ("9", "ISO 15765-4 CAN 29-bit 250 kbit/s"),
)

# Physical diagnostic addresses. The Scrambler M3C answered on 7E1/7E9 first.
PHYS_ADDRS_11BIT = (
    ("7E1", "7E9"),
    ("7E0", "7E8"),
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
    service: ServiceSnapshot = field(default_factory=ServiceSnapshot)


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
        # M3C physical IDs; 7E0/7E8 is tried only if VIN at 7E1 fails.
        self._diag_tx = "7E1"
        self._diag_rx = "7E9"
        self._ecu_seen = False
        self._can_cache: dict = {}

    @property
    def connected(self) -> bool:
        return self.elm.connected

    @property
    def vehicle_connected(self) -> bool:
        """True when a VIN was read from the ECU (R7.1 / R9.2)."""
        return bool(self.snapshot.vin)

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
                LOG.info(
                    "ELM327 connection: device=%s baud=%s adapter=%s protocol=%s description=%s",
                    self.info.port,
                    self.info.baudrate,
                    self.info.adapter_id,
                    self.info.protocol,
                    self.info.description or "",
                )
                self.snapshot = self.read_vehicle()
                LOG.info(
                    "Vehicle snapshot vin=%s source=%s faults=%s ecu_ok=%s notes=%s",
                    self.snapshot.vin or "(none)",
                    self.snapshot.vin_source or "(none)",
                    len(self.snapshot.faults),
                    self.snapshot.ecu_ok,
                    self.snapshot.notes or "",
                )
                return self.info
            except Exception as exc:
                last_error = f"{cand.device}: {exc}"
                LOG.warning("Connect attempt failed: %s", last_error)
                self.elm.close()
                self.info = None
        raise ElmError(last_error)

    def disconnect(self) -> None:
        info = self.info
        if self.elm.connected or info:
            if info:
                LOG.info(
                    "ELM327 disconnection: device=%s baud=%s adapter=%s protocol=%s",
                    info.port,
                    info.baudrate,
                    info.adapter_id,
                    info.protocol,
                )
            else:
                LOG.info("ELM327 disconnection: device=%s", self.elm.port_name or "(unknown)")
        self.elm.close()
        self.info = None
        self.snapshot = VehicleSnapshot()
        self._ecu_seen = False
        self._can_cache = {}

    def read_vehicle(self) -> VehicleSnapshot:
        snap = VehicleSnapshot()
        if self.info:
            snap.protocol = self.info.protocol
        vin, source = self._read_vin()
        snap.vin = vin
        snap.vin_source = source
        snap.faults = self._read_faults()
        snap.ecu_ok = bool(vin) or bool(snap.faults) or self._ecu_seen or self._ping_ecu()
        if not vin:
            snap.notes = "No vehicle detected."
        snap.service = self.read_service(vehicle_present=snap.ecu_ok or bool(vin))
        self.snapshot = snap
        return snap

    def read_live(self, key: str) -> Optional[float]:
        """Mode 01 first only on a silent KWP ECU; otherwise CAN/ATRV."""
        if key == NONE_KEY or key not in LIVE_PARAMS:
            return None
        param = LIVE_PARAMS[key]
        if param.obd_cmd == "ATRV":
            raw = self.elm.command("ATRV", timeout=0.6)
            volts = parse_atrv(raw)
            if volts is not None:
                return volts
        elif not self._ecu_seen:
            # Passenger-car PIDs. Skip once KWP has answered — 010C etc. are NO DATA
            # and leave STOPPED in the adapter.
            raw = self.elm.command(param.obd_cmd, timeout=0.5)
            if not response_failed(raw):
                data = mode01_payload(parse_hex_bytes(raw), param.pid)
                if data:
                    value = param.decode(data)
                    if value is not None:
                        return value
        if param.can_id is not None and param.can_decode is not None:
            try:
                frame = self._sniff_frame(param.can_id)
                if frame:
                    return param.can_decode(frame)
            except Exception as exc:
                LOG.debug("CAN sniff %s failed: %s", param.key, exc)
        return None

    def _sniff_frame(self, can_id: int) -> Optional[bytes]:
        now = time.monotonic()
        hit = self._can_cache.get(can_id)
        if hit and now - hit[0] < 0.3:
            return hit[1]
        sniffed = self.elm.sniff_can(can_id)
        frame = can_payload(sniffed, can_id)
        self._can_cache[can_id] = (now, frame)
        try:
            # ATMA used ATCRA<id>; put 7E1/7E9 back for the next diagnostic command.
            self._address_ecu(self._diag_tx, self._diag_rx)
        except Exception:
            pass
        return frame

    def _select_protocol(self) -> str:
        """Prefer a live ISO 15765-4 bus; default to 11-bit 500 kbit/s for the M3C."""
        last_error = "protocol select failed"
        fallback: Optional[Tuple[str, str]] = None
        for code, name in ISO15765_PROTOCOLS:
            try:
                self.elm.configure_iso15765(code)
                # 0100 is a bus presence probe, not an M3C PID. NO DATA + ISO 15765-4
                # (CAN 11/500) is the expected happy path.
                probe = self.elm.command("0100", timeout=3.0)
                reported = self.elm.protocol_name()
                label = f"{name} [{reported}]"
                upper = probe.upper()
                if "UNABLE TO CONNECT" in upper or "BUS ERROR" in upper or "CAN ERROR" in upper:
                    last_error = probe
                    if fallback is None and code == "6":
                        fallback = (code, label)
                    continue
                if parse_hex_bytes(probe):
                    LOG.info("Selected live protocol %s", label)
                    return label
                if fallback is None:
                    fallback = (code, label)
                # 500 kbit/s with NO DATA means the bus is present; skip 250 kbit/s.
                if code in ("6", "7") and "CAN ERROR" not in upper:
                    break
            except Exception as exc:
                last_error = str(exc)
        if fallback:
            code, label = fallback
            self.elm.configure_iso15765(code)
            reported = self.elm.protocol_name()
            label = f"{label.split('[')[0].strip()} [{reported}]"
            LOG.info("Using fallback ISO 15765-4 protocol %s", label)
            return label
        raise ElmError(f"ISO 15765-4 not established: {last_error}")

    def _ping_ecu(self) -> bool:
        """Mode 01 0100. False on the M3C; VIN/NRC is the real presence check."""
        resp = self.elm.command("0100", timeout=2.5)
        data = parse_hex_bytes(resp)
        return bool(data) and not response_failed(resp)

    def _read_vin(self) -> Tuple[str, str]:
        # Mode 09 PID 02 is SAE J1979; this ECU ignores it (NO DATA).
        self._ecu_seen = False
        raw = self.elm.command("0902", timeout=4.0)
        vin = _vin_from_mode09(parse_hex_bytes(raw))
        LOG.debug("VIN Mode 09 raw=%s parsed=%s", _brief(raw), vin or "(none)")
        if vin:
            self._ecu_seen = True
            return vin, "OBD Mode 09 PID 02"
        # 7E1 then 7E0. 1003 is KWP/UDS diagnostic session, not a VIN read.
        for tx, rx in PHYS_ADDRS_11BIT:
            try:
                self._address_ecu(tx, rx)
                session = self.elm.command("1003", timeout=1.5)
                LOG.debug("UDS 1003 @%s raw=%s", tx, _brief(session))
                self._note_ecu(session)
                sess_ok = parse_hex_bytes(session)[:1] == b"\x50"
                if sess_ok:
                    self._diag_tx, self._diag_rx = tx, rx
                # Do not send 22F190 here: M3C NRC 0x13. 1A90/1A91 are 2-byte KWP.
                for cmd in ("1A90", "1A91"):
                    if cmd in ("1A90", "1A91"):
                        payload = self.elm.isotp_request(cmd, timeout=3.0)
                        raw = payload.hex().upper()
                        vin = _vin_from_kwp(payload)
                    else:
                        raw = self.elm.command(cmd, timeout=4.0)
                        payload = parse_hex_bytes(raw)
                        vin = _vin_from_kwp(payload)
                    self._note_ecu(payload if cmd in ("1A90", "1A91") else raw)
                    LOG.debug(
                        "VIN KWP %s @%s raw=%s parsed=%s",
                        cmd,
                        tx,
                        _brief(raw),
                        vin or "(none)",
                    )
                    if vin:
                        self._diag_tx, self._diag_rx = tx, rx
                        return vin, f"KWP {cmd} @{tx}/{rx}"
                if sess_ok:
                    break
            except Exception as exc:
                LOG.debug("UDS VIN %s failed: %s", tx, exc)
        return "", ""

    def _note_ecu(self, raw) -> None:
        data = raw if isinstance(raw, (bytes, bytearray)) else parse_hex_bytes(str(raw))
        # 0x7F NRC still proves a tester is talking to this address.
        if data and (data[0] == 0x7F or 0x40 <= data[0] <= 0x7E):
            self._ecu_seen = True

    def _read_faults(self) -> List[FaultCode]:
        faults: List[FaultCode] = []
        if self._ecu_seen:
            try:
                self._address_ecu(self._diag_tx, self._diag_rx)
                self.elm.command("1003", timeout=1.0)
                # KWP read-DTC variants; 1902FF is 3-byte UDS and gets 0x13 here.
                for cmd in ("1800", "17FF", "13"):
                    raw = self.elm.command(cmd, timeout=2.5)
                    self._note_ecu(raw)
                    payload = parse_hex_bytes(raw)
                    if payload and payload[:1] != b"\x7f" and not response_failed(raw):
                        decoded = decode_mode03(payload) or decode_uds_dtc(payload)
                        if decoded:
                            return unique_codes(decoded)
            except Exception as extra:
                LOG.debug("UDS DTC failed: %s", extra)
            return unique_codes(faults)
        raw = self.elm.command("03", timeout=2.0)
        payload = parse_hex_bytes(raw)
        if not response_failed(raw) and not (payload and payload[:1] == b"\x7f"):
            faults.extend(decode_mode03(payload))
        if faults:
            return unique_codes(faults)
        for tx, rx in PHYS_ADDRS_11BIT:
            try:
                self._address_ecu(tx, rx)
                raw = self.elm.command("1902FF", timeout=4.0)
                if response_failed(raw):
                    continue
                decoded = decode_uds_dtc(parse_hex_bytes(raw))
                if decoded:
                    self._diag_tx, self._diag_rx = tx, rx
                    return unique_codes(decoded)
            except Exception as exc:
                LOG.debug("UDS DTC %s failed: %s", tx, exc)
        return unique_codes(faults)

    def _address_ecu(self, tx: str, rx: str) -> None:
        """ATSH = tester request ID, ATCRA = accept only the ECU response ID."""
        self.elm.command(f"ATSH{tx}", timeout=1.0)
        self.elm.command(f"ATCRA{rx}", timeout=1.0)

    def read_service(self, vehicle_present: bool) -> ServiceSnapshot:
        snap = ServiceSnapshot(vehicle_present=vehicle_present)
        if not vehicle_present:
            snap.notes = "No vehicle detected."
            return snap
        try:
            # Re-enter extended session; 0x22 DIDs below usually NRC 0x13 on M3C.
            self.elm.command("1003", timeout=0.6)
        except Exception:
            pass
        packed = self._uds_read_did(0xF1A0) or self._uds_read_did(0x0200)
        if packed:
            trio = parse_packed_service(packed)
            if trio:
                oil_km, desmo_km, interval = trio
                snap.oil_remaining_km = oil_km
                snap.desmo_remaining_km = desmo_km
                snap.interval_km = interval
                snap.oil = indicator_from_remaining(oil_km)
                snap.desmo = indicator_from_remaining(desmo_km)
                snap.source = "UDS packed service DID"
        oil_data = packed if packed and parse_packed_service(packed) is None else None
        if oil_data is None and snap.oil_remaining_km is None:
            oil_data = self._uds_read_did(0xF401) or self._uds_read_did(0x0101)
        if snap.desmo_remaining_km is None:
            desmo_data = self._uds_read_did(0xF1A1) or self._uds_read_did(0xF402)
            if desmo_data:
                snap.desmo_remaining_km = parse_remaining(desmo_data)
                snap.desmo = indicator_from_remaining(snap.desmo_remaining_km)
        if snap.interval_km is None:
            interval_data = self._uds_read_did(0xF1A2) or self._uds_read_did(0xF400)
            if interval_data:
                snap.interval_km = parse_remaining(interval_data)
        if oil_data:
            snap.oil_remaining_km = parse_remaining(oil_data)
            snap.oil = indicator_from_remaining(snap.oil_remaining_km)
        grips_data = self._uds_read_did(0xF1A3) or self._uds_read_did(0xF010)
        if grips_data:
            snap.grips = option_from_flag(parse_option_flag(grips_data))
        if snap.grips == OptionState.UNKNOWN:
            try:
                sniffed = self.elm.sniff_can(0x280)
                frame = can_payload(sniffed, 0x280)
                if frame:
                    snap.grips = option_from_flag(grips_from_can_280(frame))
            except Exception as exc:
                LOG.debug("Heated-grip CAN sniff failed: %s", exc)
        if snap.oil_remaining_km is None and snap.desmo_remaining_km is None:
            try:
                sniffed = self.elm.sniff_can(0x201)
                frame = can_payload(sniffed, 0x201)
                if frame:
                    snap.oil, snap.desmo = indicators_from_can_201(frame)
                    if not snap.source:
                        snap.source = "CAN 0x201 ECU→dash"
            except Exception as exc:
                LOG.debug("Service CAN sniff failed: %s", exc)
        if snap.source or snap.grips != OptionState.UNKNOWN:
            if not snap.source:
                snap.source = "ECU option / service data"
            return snap
        snap.notes = "Service data was not available from the ECU."
        return snap

    def _uds_read_did(self, did: int) -> Optional[bytes]:
        """SID 0x22 ReadDataByIdentifier. M3C typically replies 7F 22 13."""
        cmd = f"22{did:04X}"
        try:
            self._address_ecu(self._diag_tx, self._diag_rx)
            raw = self.elm.command(cmd, timeout=0.7)
            if response_failed(raw):
                return None
            payload = parse_hex_bytes(raw)
            if payload and payload[0] == 0x62:
                body = payload[3:] if len(payload) >= 3 else payload[1:]
                return body or None
        except Exception as exc:
            LOG.debug("UDS DID %04X failed: %s", did, exc)
        return None


def _nrc_is(raw: str, code: int) -> bool:
    """Negative response: 7F <sid> <nrc>. 0x13 = incorrectMessageLengthOrInvalidFormat."""
    data = parse_hex_bytes(raw)
    return len(data) >= 3 and data[0] == 0x7F and data[2] == code


def _brief(text: str, limit: int = 240) -> str:
    compact = " | ".join(line.strip() for line in text.splitlines() if line.strip())
    if not compact:
        return "(empty)"
    if len(compact) > limit:
        return compact[:limit] + "…"
    return compact


def _vin_from_kwp(payload: bytes) -> str:
    """ISO 14230 ReadEcuIdentification (1A) / ReadDataByLocalId (21)."""
    if not payload or payload[0] == 0x7F:
        return ""
    data = payload
    if data[:1] in (b"\x5A", b"\x61"):
        data = data[1:]
        if data:
            data = data[1:]  # local ID (0x90 for VIN)
    ascii_bytes = bytearray(b for b in data if 32 <= b < 127)
    text = bytes(ascii_bytes).decode("ascii", "ignore").replace("\x00", "").strip()
    match = VIN_RE.search(text.replace(" ", ""))
    return match.group(0) if match else ""


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
    if len(data) >= 2:
        data = data[2:]
    text = "".join(chr(b) for b in data if 32 <= b < 127)
    match = VIN_RE.search(text.replace(" ", ""))
    return match.group(0) if match else ""
