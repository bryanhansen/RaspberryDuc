# RaspberryDuc

Python OBD-II diagnostic utility for a **2015 Ducati Scrambler Icon 800** (Continental M3C ECU), built for a Raspberry Pi with a **480×320** touchscreen and an **ELM327 USB** adapter.

## Features

- Autodetects an ELM327 on a USB virtual COM port (`/dev/ttyUSB*`, `/dev/ttyACM*`)
- Speaks **ISO 11898 / ISO 15765-4** (CAN 11/29-bit, 250/500 kbit/s; M3C defaults to 11-bit 500 kbit/s)
- Status bar always shows ELM327 state, COM port, and Connect / Disconnect
- **Main** tab: VIN and active fault codes after a successful connect; shows **No vehicle detected** if the adapter is up but VIN cannot be read
- **Service** tab: oil and desmo service indicators, heated-grip ECU activation, and service interval
- **Live** tab: four selectable live value panels, update rate (default 1 s), and Start/Stop
- **About** tab: version, copyright (Bryan Hansen), scrollable GPLv3 license text
- Optional ECU transaction logging via `raspberryduc.ini`

## Hardware

1. Raspberry Pi with 480×320 display (framebuffer mode `480x320` is expected)
2. ELM327 USB interface (genuine v1.4 / STN11xx recommended; many USB clones work)
3. Ducati 4-pin DDA / SICMA to 16-pin OBD adapter on the bike diagnostic connector
4. Ignition **ON** (engine may be off)

The `pi` user must be in the `dialout` group (Raspbian default).

## Run

```bash
cd /home/pi/RaspberryDuc
python3 -m pip install --user -r requirements.txt
python3 run.py
```

On a graphical session:

```bash
DISPLAY=:0 python3 /home/pi/RaspberryDuc/run.py
```

Press **Escape** to quit fullscreen during development.

A desktop launcher is in `scripts/raspberryduc.desktop`. Copy it to `~/.local/share/applications/` or `~/.config/autostart/` to start at login.

## Logging (R20)

ELM327 / ECU traffic is on by default. Edit `raspberryduc.ini` next to `run.py` to turn it off:

```ini
[logging]
enabled = false
directory = logs
```

Restart the app. A new file is created under `logs/` as `raspberryduc_YYYY_DD_MM_HH_MM_SS.log` if it does not already exist. At most five session logs are kept; the oldest is deleted on start. Connect and Disconnect each write an entry (device, baud rate, adapter, protocol). The same file records every TX command and RX response. Each line is flushed and fsynced so a power-off still keeps the log. You can also point at another ini with `RASPBERRYDUC_CONFIG=/path/to/file.ini`.

## Protocol notes

The M3C is **KWP2000 on ISO 15765-4 CAN 11-bit 500 kbit/s**, not a passenger-car UDS server. Physical addressing that works is tester **7E1** / ECU **7E9**. Session `1003` returns `5003`. VIN is **KWP `1A90`** over raw ISO-TP (CAF off); Mode 09 `0902` and UDS `22F190` do not. Three-byte UDS (`22…`, `1902FF`) returns NRC `0x13`. Live values use broadcast CAN (`0x081` throttle, `0x100` RPM, `0x201` voltage) and ATRV, not Mode 01. If the ELM327 connects but no VIN is returned, the Main tab shows **No vehicle detected** and Live Start stays disabled.

## License

Copyright (C) 2026 Bryan Hansen. GNU GPL v3 or later. See `LICENSE`.
