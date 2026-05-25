# Grid-Guardian Edge Module

Raspberry Pi edge integration layer for the Grid-Guardian microgrid management system.

## Overview

This module connects real-world hardware (PZEM-004T energy sensor + relay) with your existing Grid-Guardian backend, AI decision engine, and blockchain settlement system.

```
Raspberry Pi (PZEM sensor)
         ↓
Edge Python Runtime (this module)
         ↓
Backend APIs / MQTT
         ↓
Dashboard + AI + Blockchain
```

## Architecture

```
grid_guardian_edge/
├── config.py              # Centralized configuration
├── sensor_reader.py       # PZEM-004T energy meter interface
├── telemetry_client.py    # HTTP/MQTT telemetry transmission
├── ai_adapter.py          # AI decision engine interface
├── relay_control.py       # GPIO relay control with safety features
├── command_listener.py    # HTTP/MQTT command receiver
├── edge_runtime.py        # Main orchestration loop
├── requirements.txt       # Python dependencies
└── services/
    └── gridguardian-edge.service  # systemd service file
```

## Features

- **Dual Communication**: HTTP (primary) + MQTT (redundant)
- **Offline Buffering**: Stores telemetry when backend is down
- **Retry Mechanism**: Automatic retry with exponential backoff
- **Safety Features**: Safe mode, voltage/current protection, manual override
- **Mock Mode**: Runs without hardware for testing
- **Command ACK**: Acknowledges received commands
- **Idempotent Commands**: Prevents duplicate command execution

## Hardware Requirements

### Raspberry Pi
- Raspberry Pi 3B+, 4, or Zero 2 W
- Raspberry Pi OS (Bullseye or later)

### PZEM-004T Energy Meter
- Connect via UART (GPIO 14/15)
- TX → RX, RX → TX, GND → GND
- 5V power supply recommended

### Relay Module (Optional)
- 5V relay module
- Connect signal to GPIO 17 (configurable)
- Use optocoupler for isolation

### Wiring Diagram
```
Raspberry Pi           PZEM-004T
-----------           ----------
GPIO 14 (TX) -------> RX
GPIO 15 (RX) <------- TX
GND          -------> GND
5V           -------> VCC

Raspberry Pi           Relay Module
-----------           ------------
GPIO 17      -------> IN/Signal
GND          -------> GND
5V           -------> VCC
```

## Installation

### 1. Copy to Raspberry Pi

```bash
# On your development machine
scp -r grid_guardian_edge pi@<pi-ip>:/home/pi/

# Or use rsync for updates
rsync -avz --exclude 'venv' --exclude '__pycache__' \
  grid_guardian_edge/ pi@<pi-ip>:/home/pi/grid_guardian_edge/
```

### 2. Create Virtual Environment

```bash
# SSH into Pi
ssh pi@<pi-ip>

# Navigate to module
cd /home/pi/grid_guardian_edge

# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

Create a `.env` file or edit environment variables in the systemd service:

```bash
# Required settings
export NODE_ID="gg-node-01"
export BACKEND_URL="http://YOUR_BACKEND_IP:3000"

# MQTT settings (optional but recommended)
export MQTT_ENABLED="true"
export MQTT_BROKER="YOUR_MQTT_BROKER_IP"
export MQTT_PORT="1883"

# Hardware settings
export SERIAL_PORT="/dev/serial0"
export RELAY_PIN="17"

# Logging
export LOG_LEVEL="INFO"
```

### 4. Enable UART on Raspberry Pi

```bash
# Edit config
sudo nano /boot/config.txt

# Add these lines:
enable_uart=1
dtoverlay=disable-bt

# Reboot
sudo reboot
```

### 5. Test Run

```bash
# Activate venv
source venv/bin/activate

# Print configuration
python edge_runtime.py --config

# Run single test iteration
python edge_runtime.py --test

# Run full runtime
python edge_runtime.py
```

### 6. Install as systemd Service

```bash
# Copy service file
sudo cp services/gridguardian-edge.service /etc/systemd/system/

# Edit configuration
sudo nano /etc/systemd/system/gridguardian-edge.service
# Update BACKEND_URL, MQTT_BROKER, NODE_ID

# Reload systemd
sudo systemctl daemon-reload

# Enable service
sudo systemctl enable gridguardian-edge

# Start service
sudo systemctl start gridguardian-edge

# Check status
sudo systemctl status gridguardian-edge

# View logs
sudo journalctl -u gridguardian-edge -f
```

## API Integration

The edge module integrates with these backend APIs:

### Telemetry
- `POST /api/telemetry` - Send sensor readings
- MQTT: `gridguardian/{node_id}/telemetry`

### AI Decisions
- `POST /api/ai/infer/{node_id}` - Get AI decision

### Commands
- `GET /api/commands/{node_id}` - Poll for commands
- MQTT: `gridguardian/{node_id}/commands`

### Status/Alerts
- `POST /api/status` - Send status updates
- `POST /api/alerts` - Send alerts

## Command Reference

Supported commands from backend:

| Command | Description |
|---------|-------------|
| `relay_on` | Turn relay ON |
| `relay_off` | Turn relay OFF |
| `safe_mode` | Enable safe mode (relay forced OFF) |
| `ping` | Health check |
| `shutdown` | Graceful shutdown |
| `execute_trade` | Execute energy trade |
| `discharge` | Discharge energy |
| `charge` | Charge/store energy |

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `NODE_ID` | `gg-node-01` | Unique node identifier |
| `BACKEND_URL` | `http://127.0.0.1:3000` | Backend API URL |
| `MQTT_ENABLED` | `true` | Enable MQTT |
| `MQTT_BROKER` | `127.0.0.1` | MQTT broker address |
| `SERIAL_PORT` | `/dev/serial0` | PZEM serial port |
| `BAUD_RATE` | `9600` | Serial baud rate |
| `RELAY_PIN` | `17` | GPIO pin for relay |
| `LOOP_INTERVAL` | `5.0` | Main loop interval (seconds) |
| `LOG_LEVEL` | `INFO` | Logging level |

## Test Checklist

- [ ] PZEM sensor connected and reading data
- [ ] Telemetry visible in dashboard
- [ ] AI decisions being received
- [ ] Relay responds to commands
- [ ] MQTT messages flowing
- [ ] Service starts on boot
- [ ] Safe mode works correctly
- [ ] Offline buffering works

## Troubleshooting

### No sensor data
```bash
# Check serial permissions
ls -la /dev/serial0
# Should be accessible by user

# Add user to dialout group
sudo usermod -a -G dialout pi
# Logout and login again
```

### GPIO permission denied
```bash
# Add user to gpio group
sudo usermod -a -G gpio pi
```

### Backend connection failed
```bash
# Test connectivity
curl http://YOUR_BACKEND_IP:3000/api/system/health

# Check firewall
sudo ufw status
```

### Service won't start
```bash
# Check logs
sudo journalctl -u gridguardian-edge -n 50

# Validate service file
sudo systemd-analyze verify gridguardian-edge.service
```

## Development

### Running in Mock Mode

The module automatically enters mock mode when:
- Serial port is not available
- RPi.GPIO is not installed
- Running on non-Pi hardware

This allows development and testing on any machine.

### Debug Logging

```bash
export LOG_LEVEL=DEBUG
python edge_runtime.py
```

## License

Part of the Grid-Guardian project.
