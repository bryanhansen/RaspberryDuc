"""Decode SAE J2012 / ISO 15031-6 and UDS ISO 14229 DTC payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional


_FIRST_LETTER = {
    0x0: "P0",
    0x1: "P1",
    0x2: "P2",
    0x3: "P3",
    0x4: "C0",
    0x5: "C1",
    0x6: "C2",
    0x7: "C3",
    0x8: "B0",
    0x9: "B1",
    0xA: "B2",
    0xB: "B3",
    0xC: "U0",
    0xD: "U1",
    0xE: "U2",
    0xF: "U3",
}

# Compact set of common powertrain descriptions for a 480x320 list.
_DESCRIPTIONS = {
    "P0100": "MAF circuit",
    "P0101": "MAF range/performance",
    "P0102": "MAF low input",
    "P0103": "MAF high input",
    "P0110": "IAT circuit",
    "P0113": "IAT high input",
    "P0115": "ECT circuit",
    "P0117": "ECT low input",
    "P0118": "ECT high input",
    "P0120": "TPS/Pedal A circuit",
    "P0121": "TPS range/performance",
    "P0122": "TPS low input",
    "P0123": "TPS high input",
    "P0130": "O2 sensor circuit B1S1",
    "P0135": "O2 heater B1S1",
    "P0170": "Fuel trim B1",
    "P0171": "System too lean B1",
    "P0172": "System too rich B1",
    "P0201": "Injector 1 circuit",
    "P0202": "Injector 2 circuit",
    "P0217": "Engine over-temp",
    "P0230": "Fuel pump primary",
    "P0261": "Cyl 1 injector low",
    "P0264": "Cyl 2 injector low",
    "P0300": "Random misfire",
    "P0301": "Cyl 1 misfire",
    "P0302": "Cyl 2 misfire",
    "P0335": "CKP sensor A circuit",
    "P0340": "CMP sensor A circuit",
    "P0351": "Ignition coil A primary",
    "P0352": "Ignition coil B primary",
    "P0440": "EVAP system",
    "P0500": "Vehicle speed sensor",
    "P0505": "Idle control system",
    "P0560": "System voltage",
    "P0562": "System voltage low",
    "P0563": "System voltage high",
    "P0600": "Serial communication",
    "P0601": "ECM memory checksum",
    "P0606": "ECM/PCM processor",
    "P0650": "MIL control circuit",
    "P1135": "O2 heater (mfr)",
    "P1336": "CKP variation",
    "P1684": "Battery disconnected",
    "U0001": "CAN high-speed bus",
    "U0100": "Lost comm with ECM",
    "U0101": "Lost comm with TCM",
    "U0155": "Lost comm with IPC",
}


@dataclass(frozen=True)
class FaultCode:
    code: str
    description: str = ""
    raw: str = ""
    status: Optional[int] = None

    def display(self) -> str:
        extra = f" — {self.description}" if self.description else ""
        return f"{self.code}{extra}"


def sae_code_from_bytes(b0: int, b1: int) -> str:
    """Two-byte ISO 15031 packing: high nibble of b0 selects P/C/B/U."""
    prefix = _FIRST_LETTER[(b0 >> 4) & 0xF]
    return f"{prefix}{b0 & 0x0F:X}{b1:02X}"


def describe(code: str) -> str:
    return _DESCRIPTIONS.get(code, "Manufacturer / unknown")


def decode_mode03(payload: bytes) -> List[FaultCode]:
    """ISO 15031-5 $03/$07. Reject SID 0x7F so ``7F 03 11`` is not P0133."""
    if negative_response(payload):
        return []
    data = _strip_sid(payload, (0x43, 0x47))
    codes: List[FaultCode] = []
    # Skip a leading count byte when the remainder is an odd length.
    if data and (len(data) % 2 == 1):
        data = data[1:]
    for i in range(0, len(data) - 1, 2):
        b0, b1 = data[i], data[i + 1]
        if b0 == 0 and b1 == 0:
            continue
        code = sae_code_from_bytes(b0, b1)
        codes.append(FaultCode(code=code, description=describe(code), raw=f"{b0:02X}{b1:02X}"))
    return codes


def decode_uds_dtc(payload: bytes) -> List[FaultCode]:
    """Parse UDS 0x19 reportDTCByStatusMask (SID 0x59 already optional)."""
    if negative_response(payload):
        return []
    data = bytearray(payload)
    if data and data[0] in (0x19, 0x59):
        data = data[1:]
    if data and data[0] == 0x02:
        data = data[1:]
    if data:
        data = data[1:]  # DTCStatusAvailabilityMask; records are then 3+status
    codes: List[FaultCode] = []
    for i in range(0, len(data) - 3, 4):
        hi, mid, lo, status = data[i], data[i + 1], data[i + 2], data[i + 3]
        if hi == 0 and mid == 0 and lo == 0:
            continue
        sae = sae_code_from_bytes(hi, mid)
        raw = f"{hi:02X}{mid:02X}{lo:02X}"
        label = f"{sae} ({raw})"
        codes.append(
            FaultCode(
                code=label,
                description=describe(sae),
                raw=raw,
                status=status,
            )
        )
    return codes


def negative_response(payload: bytes) -> bool:
    """ISO 14229/15031 NRC (SID 0x7F). Service-not-supported is 0x11, not a DTC."""
    return bool(payload) and payload[0] == 0x7F


def unique_codes(items: Iterable[FaultCode]) -> List[FaultCode]:
    seen = set()
    out: List[FaultCode] = []
    for item in items:
        if item.code in seen:
            continue
        seen.add(item.code)
        out.append(item)
    return out


def _strip_sid(payload: bytes, sids: tuple) -> bytes:
    if payload and payload[0] in sids:
        return payload[1:]
    return payload
