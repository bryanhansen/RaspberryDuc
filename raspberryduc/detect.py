"""USB virtual COM port autodetection for ELM327 adapters.

The Pi adapter enumerates as FT232R (VID 0x0403), not a string containing ELM.
Bluetooth RFCOMM and the Pi's onboard UART (ttyAMA0 / serial0) are skipped.
"""

from __future__ import annotations

import glob
import os
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from serial.tools import list_ports


# Common USB-serial bridge chips used by ELM327 clones and genuine interfaces.
_USB_HINTS = (
    "ELM",
    "OBD",
    "STN11",
    "SCANTOOL",
    "FTDI",
    "FT232",
    "CP210",
    "SILICON LABS",
    "CH340",
    "CH341",
    "QINHENG",
    "PROLIFIC",
    "PL2303",
    "USB SERIAL",
    "USB-SERIAL",
    "UART",
)

_SKIP_HINTS = (
    "BLUETOOTH",
    "RFCOMM",
    "DEBUG",
    "CONSOLE",
)

_VID_HINTS = {
    0x0403,  # FTDI
    0x10C4,  # Silicon Labs CP210x
    0x1A86,  # QinHeng CH340
    0x067B,  # Prolific
    0x04D8,  # Microchip (some genuine ELM)
    0x2FDE,  # OBDLink / ScanTool
}


@dataclass(frozen=True)
class SerialCandidate:
    device: str
    description: str
    hwid: str
    vid: Optional[int]
    pid: Optional[int]
    score: int


def list_candidates() -> List[SerialCandidate]:
    found: List[SerialCandidate] = []
    seen = set()
    for port in list_ports.comports():
        device = port.device
        if not device or device in seen:
            continue
        seen.add(device)
        blob = " ".join(
            str(x)
            for x in (port.description, port.manufacturer, port.hwid, port.product)
            if x
        ).upper()
        if any(skip in blob for skip in _SKIP_HINTS):
            continue
        if _is_onboard_uart(device, blob):
            continue
        score = 0
        if any(hint in blob for hint in _USB_HINTS):
            score += 20
        vid = int(port.vid) if port.vid is not None else None
        pid = int(port.pid) if port.pid is not None else None
        if vid in _VID_HINTS:
            score += 15
        if "ELM" in blob or "OBD" in blob:
            score += 30
        if _looks_usb_vcp(device):
            score += 10
        if score <= 0 and _looks_usb_vcp(device):
            score = 5
        if score > 0:
            found.append(
                SerialCandidate(
                    device=device,
                    description=port.description or "",
                    hwid=port.hwid or "",
                    vid=vid,
                    pid=pid,
                    score=score,
                )
            )
    for extra in _linux_usb_serial_globs():
        if extra not in seen:
            found.append(
                SerialCandidate(
                    device=extra,
                    description="USB serial device",
                    hwid="",
                    vid=None,
                    pid=None,
                    score=5,
                )
            )
    found.sort(key=lambda c: c.score, reverse=True)
    return found


def preferred_devices() -> Sequence[str]:
    return [c.device for c in list_candidates()]


def _looks_usb_vcp(device: str) -> bool:
    name = os.path.basename(device).upper()
    return name.startswith("TTYUSB") or name.startswith("TTYACM") or name.startswith("COM")


def _is_onboard_uart(device: str, blob: str) -> bool:
    """Pi debug UART — not the USB ELM. Matching it hangs ATZ on the console port."""
    name = os.path.basename(device).lower()
    if name in {"ttyama0", "ttyama10", "serial0", "serial1", "ttyS0"}:
        return True
    return "3F201000" in blob or "FE201000" in blob


def _linux_usb_serial_globs() -> Iterable[str]:
    # pyserial can miss a just-plugged FT232 until /dev/ttyUSB* is listed raw.
    if sys.platform != "linux":
        return []
    paths = []
    paths.extend(sorted(glob.glob("/dev/ttyUSB*")))
    paths.extend(sorted(glob.glob("/dev/ttyACM*")))
    return paths
