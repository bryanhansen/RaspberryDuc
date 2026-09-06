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

## Protocol notes

The M3C is addressed as a standard ISO 15765-4 diagnostic server (`7E0`/`7E8`, then `7E1`/`7E9`). VIN is requested with OBD Mode 09 PID 02, then UDS DID `F190`. Faults use Mode 03 / 07, then UDS `19 02 FF`. Service status is read with UDS maintenance DIDs, then CAN ID `0x201` (ECU→dash warning bits).

The Scrambler does not always implement passenger-car Mode 01 PIDs. Realtime values first use ISO 15765-4 Mode 01, then fall back to known M3C CAN frames (RPM, throttle, voltage) when needed. If the ELM327 connects but no VIN is returned, the Main tab shows **No vehicle detected** and Realtime Start stays disabled.

## License

Copyright (C) 2026 Bryan Hansen. GNU GPL v3 or later. See `LICENSE`.
