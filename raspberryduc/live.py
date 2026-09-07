"""Live OBD-II / ISO 15765-4 value catalog and decoders.

Mode 01 formulas are SAE J1979 (cars). The Scrambler never answers them; the
``can_id`` / ``can_decode`` fields are the M3C broadcast fallbacks used once
``_ecu_seen`` is set. ATRV is adapter pin voltage, not an ECU DID.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


Decoder = Callable[[bytes], Optional[float]]


@dataclass(frozen=True)
class LiveParam:
    key: str
    label: str
    units: str
    obd_cmd: str  # ELM request, e.g. 010C or ATRV
    pid: int
    decode: Decoder
    can_id: Optional[int] = None
    can_decode: Optional[Decoder] = None


def mode01_payload(data: bytes, pid: int) -> Optional[bytes]:
    """Return data bytes after a Mode 01 positive response for pid."""
    i = 0
    while i + 1 < len(data):
        if data[i] == 0x41 and data[i + 1] == pid:
            return data[i + 2 :]
        i += 1
    return None


def _a(data: Optional[bytes]) -> Optional[int]:
    if not data:
        return None
    return data[0]


def _ab(data: Optional[bytes]) -> Optional[Tuple[int, int]]:
    if not data or len(data) < 2:
        return None
    return data[0], data[1]


def decode_percent(data: bytes) -> Optional[float]:
    a = _a(data)
    return None if a is None else a * 100.0 / 255.0


def decode_temp_c(data: bytes) -> Optional[float]:
    a = _a(data)
    return None if a is None else float(a - 40)


def decode_rpm(data: bytes) -> Optional[float]:
    pair = _ab(data)
    return None if pair is None else ((pair[0] * 256) + pair[1]) / 4.0


def decode_speed(data: bytes) -> Optional[float]:
    a = _a(data)
    return None if a is None else float(a)


def decode_timing(data: bytes) -> Optional[float]:
    a = _a(data)
    return None if a is None else (a / 2.0) - 64.0


def decode_fuel_kpa(data: bytes) -> Optional[float]:
    a = _a(data)
    return None if a is None else float(a * 3)


def decode_maf(data: bytes) -> Optional[float]:
    pair = _ab(data)
    return None if pair is None else ((pair[0] * 256) + pair[1]) / 100.0


def decode_voltage_0142(data: bytes) -> Optional[float]:
    pair = _ab(data)
    return None if pair is None else ((pair[0] * 256) + pair[1]) / 1000.0


def decode_runtime(data: bytes) -> Optional[float]:
    pair = _ab(data)
    return None if pair is None else float((pair[0] * 256) + pair[1])


def decode_fuel_level(data: bytes) -> Optional[float]:
    return decode_percent(data)


def decode_scrambler_tps(frame: bytes) -> Optional[float]:
    """CAN 0x081. Observed idle ``00 01 …`` (byte1) and ``… C8 …`` (byte4 = 100%)."""
    if not frame:
        return None
    for idx in (0, 1, 4):
        if idx < len(frame) and 0 < frame[idx] <= 0xC8:
            return min(100.0, frame[idx] * 100.0 / 0xC8)
    if frame[0] == 0:
        return 0.0
    return None


def decode_scrambler_coolant(frame: bytes) -> Optional[float]:
    """Prefer a plausible coolant byte (engine temp, not 12 V / 0xC8 scale)."""
    if not frame:
        return None
    for idx in (0, 1, 2, 5, 6):
        if idx >= len(frame):
            continue
        temp = float(frame[idx] - 40)
        if -20.0 <= temp <= 130.0 and frame[idx] not in (0x00, 0xFF):
            return temp
    return None


def decode_scrambler_rpm(frame: bytes) -> Optional[float]:
    """CAN 0x100 bytes 3-4 hold RPM on Scrambler / Monster-style M3C buses."""
    if len(frame) < 5:
        return None
    return float((frame[3] << 8) | frame[4])


def decode_scrambler_voltage(frame: bytes) -> Optional[float]:
    """CAN 0x201 last byte is often tenths of a volt (0x7D = 12.5 V)."""
    if not frame:
        return None
    return frame[-1] / 10.0


NONE_KEY = "none"
NONE_LABEL = "None"

LIVE_PARAMS: Dict[str, LiveParam] = {}


def _register(param: LiveParam) -> LiveParam:
    LIVE_PARAMS[param.key] = param
    return param


# Mode 01 command is kept for cars; can_id is what the Scrambler actually streams.
_register(LiveParam("rpm", "Engine RPM", "rpm", "010C", 0x0C, decode_rpm, 0x100, decode_scrambler_rpm))
_register(LiveParam("speed", "Vehicle speed", "km/h", "010D", 0x0D, decode_speed))
_register(LiveParam("coolant", "Coolant temp", "°C", "0105", 0x05, decode_temp_c, 0x110, decode_scrambler_coolant))
_register(LiveParam("iat", "Intake temp", "°C", "010F", 0x0F, decode_temp_c, 0x110, decode_scrambler_coolant))
_register(LiveParam("tps", "Throttle", "%", "0111", 0x11, decode_percent, 0x081, decode_scrambler_tps))
_register(LiveParam("load", "Engine load", "%", "0104", 0x04, decode_percent))
_register(LiveParam("timing", "Timing adv.", "°", "010E", 0x0E, decode_timing))
_register(LiveParam("fuel_press", "Fuel pressure", "kPa", "010A", 0x0A, decode_fuel_kpa))
_register(LiveParam("maf", "MAF", "g/s", "0110", 0x10, decode_maf))
_register(LiveParam("mod_v", "Module voltage", "V", "0142", 0x42, decode_voltage_0142, 0x201, decode_scrambler_voltage))
_register(LiveParam("runtime", "Run time", "s", "011F", 0x1F, decode_runtime))
_register(LiveParam("fuel_lvl", "Fuel level", "%", "012F", 0x2F, decode_fuel_level))
_register(LiveParam("batt_v", "Adapter voltage", "V", "ATRV", 0x00, lambda _d: None, 0x201, decode_scrambler_voltage))


def choices() -> List[Tuple[str, str]]:
    items = [(NONE_KEY, NONE_LABEL)]
    items.extend((p.key, p.label) for p in LIVE_PARAMS.values())
    return items


def format_value(key: str, value: Optional[float]) -> str:
    if value is None:
        return "—"
    if key in {"rpm", "runtime", "speed"}:
        return f"{value:.0f}"
    if key in {"mod_v", "batt_v", "maf"}:
        return f"{value:.1f}"
    return f"{value:.0f}"


def parse_atrv(text: str) -> Optional[float]:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text or "")
    return float(match.group(1)) if match else None


def can_payload(response: str, can_id: int) -> Optional[bytes]:
    """Parse ATMA text.

    ATH1+ID: ``081`` + 16 hex data digits (or spaced). CRA-only dumps are exactly
    16 hex chars with no ID — those were dropped before and live stayed blank.
    """
    want = f"{can_id:03X}"
    named: Optional[bytes] = None
    filtered: Optional[bytes] = None
    for line in response.replace(",", " ").replace("|", "\n").splitlines():
        upper = line.strip().upper()
        if not upper or upper.startswith("AT") or "STOPPED" in upper or "BUFFER" in upper:
            continue
        hex_only = "".join(ch for ch in upper if ch in "0123456789ABCDEF")
        if len(hex_only) >= 19 and hex_only[:3] == want:
            body = hex_only[3:]
            if len(body) % 2 == 1:
                body = body[:-1]
            if len(body) >= 2:
                try:
                    named = bytes.fromhex(body[:16] if len(body) >= 16 else body)
                    continue
                except ValueError:
                    pass
        if len(hex_only) == 16:
            try:
                filtered = bytes.fromhex(hex_only)
            except ValueError:
                pass
            continue
        parts = upper.split()
        if len(parts) >= 2:
            ident = parts[0].lstrip("0") or "0"
            if ident.zfill(3)[-3:] == want:
                hex_bytes = []
                for part in parts[1:]:
                    if all(ch in "0123456789ABCDEF" for ch in part) and len(part) % 2 == 0:
                        hex_bytes.append(part)
                blob = "".join(hex_bytes)
                if blob:
                    try:
                        named = bytes.fromhex(blob[:16] if len(blob) >= 16 else blob)
                    except ValueError:
                        pass
    return named or filtered
