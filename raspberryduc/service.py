"""Oil / Desmo service and heated-grip option decoding for the Continental M3C."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class Indicator(str, Enum):
    OK = "OK"
    DUE = "Due"
    UNKNOWN = "Unknown"


class OptionState(str, Enum):
    ON = "Activated"
    OFF = "Not activated"
    UNKNOWN = "Unknown"


@dataclass
class ServiceSnapshot:
    vehicle_present: bool = False
    oil: Indicator = Indicator.UNKNOWN
    desmo: Indicator = Indicator.UNKNOWN
    grips: OptionState = OptionState.UNKNOWN
    interval_km: Optional[int] = None
    oil_remaining_km: Optional[int] = None
    desmo_remaining_km: Optional[int] = None
    source: str = ""
    notes: str = ""


def indicator_from_remaining(value: Optional[int]) -> Indicator:
    if value is None:
        return Indicator.UNKNOWN
    return Indicator.DUE if value <= 0 else Indicator.OK


def option_from_flag(flag: Optional[bool]) -> OptionState:
    if flag is None:
        return OptionState.UNKNOWN
    return OptionState.ON if flag else OptionState.OFF


def plausible_km(value: int) -> bool:
    return 0 <= value <= 200000


def u16(data: bytes, offset: int = 0) -> Optional[int]:
    if len(data) < offset + 2:
        return None
    value = (data[offset] << 8) | data[offset + 1]
    return value if plausible_km(value) else None


def parse_packed_service(data: bytes) -> Optional[Tuple[int, int, int]]:
    """oil remaining, desmo remaining, interval (km) as three big-endian u16s.

    M3C UDS 0x22 DIDs currently return NRC 0x13, so this path is unused until
    a KWP local-ID equivalent is found. CAN 0x201 is the live fallback.
    """
    if len(data) < 6:
        return None
    oil = u16(data, 0)
    desmo = u16(data, 2)
    interval = u16(data, 4)
    if oil is None or desmo is None or interval is None:
        return None
    return oil, desmo, interval


def parse_remaining(data: bytes) -> Optional[int]:
    if not data:
        return None
    if len(data) >= 4:
        value = int.from_bytes(data[:4], "big")
        if plausible_km(value) and value > 255:
            return value
    return u16(data, 0)


def parse_option_flag(data: bytes) -> Optional[bool]:
    """Interpret a UDS option byte: 0/1 or bit0 of the first byte."""
    if not data:
        return None
    if data[0] in (0x00, 0x01):
        return bool(data[0])
    return bool(data[0] & 0x01)


def indicators_from_can_201(frame: bytes) -> Tuple[Indicator, Indicator]:
    """ECU→dash warning bitmap on 0x201. bit0 oil, bit1 desmo."""
    if not frame:
        return Indicator.UNKNOWN, Indicator.UNKNOWN
    if all(b == 0 for b in frame):
        return Indicator.OK, Indicator.OK
    oil = Indicator.DUE if (frame[0] & 0x01) else Indicator.OK
    desmo = Indicator.DUE if (frame[0] & 0x02) else Indicator.OK
    return oil, desmo


def grips_from_can_280(frame: bytes) -> Optional[bool]:
    """
    Dash/feature coding on 0x280. Heated-grip enable is commonly a low bit
    in the feature field used by Ducati 11-bit buses.
    """
    if not frame:
        return None
    if len(frame) >= 3:
        return bool(frame[2] & 0x01)
    return bool(frame[0] & 0x10)
