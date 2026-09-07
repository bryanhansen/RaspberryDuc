"""Application identity and About-tab legal notices."""

from pathlib import Path

__version__ = "0.5.10"
APP_NAME = "RaspberryDuc"
APP_TITLE = "Ducati Scrambler OBD-II"
COPYRIGHT = "Copyright (C) 2026 Bryan Hansen"
LICENSE_NAME = "GNU General Public License v3.0 or later"
ECU_TARGET = "Continental M3C (2015 Ducati Scrambler Icon 800)"

GPL_NOTICE = """This program is free software: you can redistribute it
and/or modify it under the terms of the GNU General
Public License as published by the Free Software
Foundation, either version 3 of the License, or (at
your option) any later version.

This program is distributed in the hope that it will
be useful, but WITHOUT ANY WARRANTY; without even the
implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public
License for more details.

You should have received a copy of the GNU General
Public License along with this program. If not, see
<https://www.gnu.org/licenses/>.
"""


def license_text() -> str:
    """Full About-tab license body, including the LICENSE file when present."""
    chunks = [COPYRIGHT, LICENSE_NAME, "", GPL_NOTICE.strip(), ""]
    license_path = Path(__file__).resolve().parent.parent / "LICENSE"
    try:
        chunks.append(license_path.read_text(encoding="utf-8"))
    except OSError:
        chunks.append("The complete GNU GPLv3 text is available at:")
        chunks.append("https://www.gnu.org/licenses/gpl-3.0.txt")
    return "\n".join(chunks).strip() + "\n"
