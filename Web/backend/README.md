# Grid-Guardian Unified Backend Layer

Unified backend service integrating Blockchain, AI Inference, and Hardware communication for the Grid-Guardian energy trading platform.

## Features

- **REST API** - Comprehensive REST endpoints for dashboard, nodes, trades, and AI inference
- **Socket.io** - Real-time updates for telemetry and trade events
- **MQTT Bridge** - Integration with Raspberry Pi edge devices
- **Blockchain Integration** - Ethers.js v6 for smart contract interactions
- **AI Service** - Placeholder for AI model inference (CQL policy)
- **MongoDB** - Telemetry, AI results, and blockchain logs storage
- **PostgreSQL** - Persistent storage for trades
- **Winston Logging** - Comprehensive logging to file and console

## Quick Start

### Prerequisites

- Node.js 18+
- MongoDB (local or Docker)
- PostgreSQL (local or Docker)
- MQTT Broker (Mosquitto recommended)

### Development Setup

```bash
# Install dependencies
npm install

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Start services via Docker
docker-compose up -d

# Run in development mode (with nodemon)
npm run dev
```

### Manual Service Setup (Alternative)

```bash
# Start MongoDB
docker run -d -p 27017:27017 --name mongodb mongo:latest

# Start PostgreSQL
docker run -d -p 5432:5432 --name postgres -e POSTGRES_PASSWORD=postgres postgres:latest
docker exec -it postgres psql -U postgres -c "CREATE DATABASE gridguardian;"

# Start MQTT Broker (Mosquitto)
docker run -d -p 1883:1883 --name mosquitto eclipse-mosquitto:latest
```

## Project Structure

```
backend/
├── config/
│   ├── db.mongo.js          # MongoDB connection
│   ├── db.postgres.js       # PostgreSQL connection
│   └── mqtt.config.js       # MQTT configuration
├── controllers/
│   ├── telemetry.controller.js
│   ├── dashboard.controller.js
│   └── system.controller.js
├── services/
│   ├── telemetry.service.js
│   ├── ai.service.js        # AI placeholder
│   └── blockchain.service.js # Blockchain placeholder
├── models/
│   ├── telemetry.model.js   # MongoDB model
│   ├── aiResult.model.js    # MongoDB model
│   ├── blockchainLog.model.js
│   └── trade.model.js       # PostgreSQL model
├── routes/
│   ├── telemetry.routes.js
│   ├── dashboard.routes.js
│   └── system.routes.js
├── middleware/
│   ├── error.middleware.js
│   └── validate.middleware.js
├── utils/
│   ├── logger.js            # Winston logger
│   └── mqttClient.js        # MQTT client
├── logs/                    # Auto-created
├── app.js                   # Express app setup
├── server.js                # Entry point
├── package.json
└── .env
```

## API Endpoints

### Telemetry

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/telemetry` | Submit telemetry data |
| GET | `/api/telemetry/latest/:node_id` | Get latest telemetry |
| GET | `/api/telemetry/history/:node_id` | Get telemetry history |

### Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/dashboard/summary` | Get all nodes summary |
| GET | `/api/dashboard/node/:node_id` | Get specific node dashboard |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/system/health` | Get system health status |
| GET | `/api/system/info` | Get system information |

## MQTT Topics

Subscribe to:
- `gridguardian/+/telemetry` - Telemetry data from nodes
- `gridguardian/+/status` - Status updates from nodes
- `gridguardian/+/alerts` - Alert messages from nodes

## Telemetry Payload Format

```json
{
  "node_id": "pi-001",
  "voltage": 230,
  "current": 5.2,
  "power": 1196,
  "timestamp": 1710000000
}
```

## Dashboard Response Format

```json
{
  "node_id": "pi-001",
  "latest_power": 1200,
  "status": "ACTIVE",
  "alerts": [],
  "ai_decision": "SELL"
}
```

## Testing

### 1. Test Health Endpoint

```bash
curl http://localhost:3000/api/system/health
```

### 2. Submit Telemetry via REST

```bash
curl -X POST http://localhost:3000/api/telemetry \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": "pi-001",
    "voltage": 230,
    "current": 5.2,
    "power": 1196,
    "timestamp": 1710000000
  }'
```

### 3. Get Latest Telemetry

```bash
curl http://localhost:3000/api/telemetry/latest/pi-001
```

### 4. Get Dashboard Summary

```bash
curl http://localhost:3000/api/dashboard/summary
```

### 5. Test MQTT (using mosquitto_pub)

```bash
# Publish telemetry
mosquitto_pub -h localhost -t "gridguardian/pi-001/telemetry" \
  -m '{"voltage":230,"current":5.2,"power":1196,"timestamp":1710000000}'

# Publish status
mosquitto_pub -h localhost -t "gridguardian/pi-001/status" \
  -m '{"status":"active","battery":85}'

# Publish alert
mosquitto_pub -h localhost -t "gridguardian/pi-001/alerts" \
  -m '{"alert":"overvoltage","value":260}'
```

### 6. Test Socket.io

```javascript
const io = require('socket.io-client');
const socket = io('http://localhost:3000');

socket.on('connect', () => {
  console.log('Connected to server');
  socket.emit('subscribe', 'pi-001');
});

socket.on('telemetry', (data) => {
  console.log('Telemetry:', data);
});

socket.on('ai:decision', (data) => {
  console.log('AI Decision:', data);
});
```

## Environment Variables

```env
# Server
NODE_ENV=development
PORT=3000

# MongoDB
MONGO_URI=mongodb://localhost:27017/gridguardian

# PostgreSQL
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=gridguardian
PG_USER=postgres
PG_PASSWORD=postgres

# MQTT
MQTT_BROKER=mqtt://localhost:1883
MQTT_USERNAME=
MQTT_PASSWORD=

# CORS
CORS_ORIGIN=*

# Logging
LOG_LEVEL=info
```

## Run Commands

```bash
# Production
npm start

# Development (with auto-reload)
npm run dev

# Run Jest tests
npm test
```

## License

MIT
