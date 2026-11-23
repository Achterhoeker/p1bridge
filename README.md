# P1 Bridge

Python service for Raspberry Pi that reads DSMR/P1 meter data from a physical USB serial adapter and forwards it to:

- a virtual serial port for ser2net
- a power file with current power in Watt for AstraMeter

## What this script does

p1bridge.py:

- reads full P1 telegrams from a fixed USB serial device id
- creates a virtual PTY at /run/p1-bridge/tty
- writes complete telegrams to the PTY in non-blocking mode (small queue)
- calculates net power from:
  - 1-0:1.7.0 (import in kW)
  - 1-0:2.7.0 (export in kW)
- writes net power atomically to /run/p1-power-watt

Formula:

```text
power_watt = round((import_kw - export_kw) * 1000)
```

## Requirements

- Linux (tested as a systemd service on Raspberry Pi)
- Python 3
- pyserial

Install dependency:

```bash
python3 -m pip install pyserial
```

## Files

- p1bridge.py - bridge service script
- README.md - project documentation
- LICENSE - MIT license
- .gitignore - ignores local/generated files

## Systemd service

Example unit file at /etc/systemd/system/p1-bridge.service:

```ini
[Unit]
Description=P1 DSMR bridge for ser2net and AstraMeter
After=dev-ttyUSB0.device
Before=ser2net.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/p1-bridge/p1bridge.py
Restart=always
RestartSec=2
User=root
RuntimeDirectory=p1-bridge
RuntimeDirectoryMode=0755

[Install]
WantedBy=multi-user.target
```

Reload and enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now p1-bridge.service
sudo systemctl status p1-bridge.service
```

## AstraMeter integration

This bridge is an easy match with AstraMeter.

Use these settings:

```ini
[GENERAL]
DEVICE_TYPE = ct002
SKIP_POWERMETER_TEST = False
DASHBOARD_ENABLED = True
DASHBOARD_ALLOW_WRITE = True
LOG_LEVEL=debug

[SCRIPT]
COMMAND = /usr/local/bin/astrameter-p1.sh
THROTTLE_INTERVAL = 0
POWER_OFFSET = -10
```

POWER_OFFSET default is 0. You can tweak it up or down to get a little more or less reported import.

Example helper script (tip: place it in /usr/local/bin/astrameter-p1.sh):

```bash
#!/bin/sh
sed -n '1p' /run/p1-power-watt
```

## Other integrations

Besides ser2net, software like Domoticz can also read directly from the virtual P1 port at /run/p1-bridge/tty.

## Useful debugging commands

```bash
# Follow service logs
journalctl -u p1-bridge.service -f

# Show current power value
sed -n '1p' /run/p1-power-watt

# Check virtual port symlink
ls -l /run/p1-bridge/tty
```

## License

This project is licensed under the MIT License. See LICENSE.
