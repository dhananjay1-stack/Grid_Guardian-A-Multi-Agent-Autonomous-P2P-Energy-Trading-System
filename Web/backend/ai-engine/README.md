# Grid-Guardian AI Decision Engine Integration

## Overview

The AI Decision Engine integrates the trained CQL (Conservative Q-Learning) model with the Grid-Guardian backend to provide real-time energy trading decisions.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Grid-Guardian System                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   Pi/Edge    │───▶│  MQTT Broker │───▶│   Backend    │       │
│  │  Telemetry   │    │              │    │   Node.js    │       │
│  └──────────────┘    └──────────────┘    └──────┬───────┘       │
│                                                  │               │
│                                    HTTP/REST     │               │
│                                                  ▼               │
│                                          ┌──────────────┐       │
│                                          │   AI Engine  │       │
│                                          │   Python     │       │
│                                          │ Flask Server │       │
│                                          └──────┬───────┘       │
│                                                  │               │
│                                          ┌──────▼───────┐       │
│                                          │  CQL Model   │       │
│                                          │ TorchScript/ │       │
│                                          │    ONNX      │       │
│                                          └──────────────┘       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### 1. AI Inference Server (`ai-engine/ai_inference_server.py`)
- Flask-based Python server
- Loads the trained CQL policy model
- Processes telemetry observations
- Returns trading decisions with confidence scores

### 2. Backend AI Service (`services/ai.service.js`)
- Node.js service that calls the AI inference server
- Handles fallback to mock decisions if AI server unavailable
- Logs and stores AI decision history

### 3. AI Trading Integration (`services/aiTradingIntegration.service.js`)
- Connects AI decisions to blockchain trading
- Validates confidence thresholds
- Creates trade proposals
- Publishes commands to Pi devices via MQTT

### 4. Configuration (`config/ai.config.js`)
- Centralized configuration for AI engine
- Action mappings, safety constraints, trading thresholds

## Setup

### 1. Install Python Dependencies

```bash
cd Web/backend/ai-engine
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Add to your `.env` file:

```env
# AI Decision Engine Configuration
AI_SERVER_URL=http://127.0.0.1:5050
AI_SERVER_TIMEOUT=5000
AI_USE_MOCK_FALLBACK=true
AI_HEALTH_CHECK_INTERVAL=30000
POLICY_PACK_PATH=../../Agentic_AI/edge/policy_pack
```

### 3. Start the AI Inference Server

```bash
cd Web/backend/ai-engine
python ai_inference_server.py
```

Or with custom configuration:

```bash
AI_SERVER_HOST=0.0.0.0 AI_SERVER_PORT=5050 python ai_inference_server.py
```

### 4. Start the Backend Server

```bash
cd Web/backend
npm start
```

## API Endpoints

### AI Decision Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/ai/decision/:node_id` | Get latest AI decision for a node |
| GET | `/api/ai/history/:node_id` | Get AI decision history |
| POST | `/api/ai/infer/:node_id` | Trigger manual AI inference |
| POST | `/api/ai/infer-with-trade/:node_id` | Inference with automatic trading |
| GET | `/api/ai/status` | Get AI engine status |
| POST | `/api/ai/refresh-health` | Force health check refresh |

### AI Trading Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/ai/trades/pending` | Get all pending AI trades |
| GET | `/api/ai/trades/pending/:node_id` | Get pending trades for a node |
| GET | `/api/ai/trades/stats` | Get trading statistics |
| POST | `/api/ai/trades/:trade_id/cancel` | Cancel a pending trade |

## AI Decision Response Format

```json
{
  "node_id": "node_123",
  "decision": "SELL",
  "confidence": 0.85,
  "action_index": 5,
  "action_name": "offer_sell",
  "action_kw": -1.5,
  "trade_action": "SELL",
  "recommended_quantity": 0.8,
  "forecasted_load": 800,
  "forecasted_solar": 1200,
  "net_power_kw": 0.4,
  "model_version": "GridGuardian-CQL",
  "timestamp": "2024-03-22T12:00:00.000Z"
}
```

## Action Mapping

| Index | Name | kW | Decision | Trade |
|-------|------|-----|----------|-------|
| 0 | charge_small | +1.0 | CHARGE | - |
| 1 | charge_large | +3.0 | CHARGE | - |
| 2 | idle | 0.0 | HOLD | - |
| 3 | discharge_small | -1.0 | DISCHARGE | - |
| 4 | discharge_large | -3.0 | DISCHARGE | - |
| 5 | offer_sell | -1.5 | SELL | SELL |
| 6 | offer_hold | 0.0 | HOLD | - |

## Real-Time Updates

The system emits AI decisions via Socket.io:

```javascript
// Listen for AI decisions
socket.on('ai:decision', (data) => {
  console.log('AI Decision:', data);
});

// Listen for trade proposals
socket.on('ai:trade_proposal', (proposal) => {
  console.log('Trade Proposal:', proposal);
});

// Listen for trade executions
socket.on('ai:trade_executed', (trade) => {
  console.log('Trade Executed:', trade);
});
```

## Observation Format

The AI model expects an 18-dimensional observation vector:

| Index | Key | Description |
|-------|-----|-------------|
| 0 | soc_kwh | Battery state of charge (kWh) |
| 1 | soc_capacity_kwh | Battery capacity (kWh) |
| 2 | pv_gen_kw | Solar PV generation (kW) |
| 3 | load_kw | Load consumption (kW) |
| 4 | net_kw | Net power (kW) |
| 5 | battery_power_kw | Battery power flow (kW) |
| 6 | price_signal | Grid price signal |
| 7 | forecast_irradiance_1h | 1-hour irradiance forecast |
| 8 | forecast_irradiance_3h | 3-hour irradiance forecast |
| 9 | forecast_temp_1h | 1-hour temperature forecast |
| 10 | actual_irradiance_wm2 | Current irradiance (W/m2) |
| 11 | voltage_v | Voltage (V) |
| 12 | current_a | Current (A) |
| 13-17 | (padding) | Zero-padded |

## Safety Features

1. **Safety Clipping**: Actions are clipped to prevent SoC violations
2. **Confidence Threshold**: Trades only trigger above 60% confidence
3. **Quantity Limits**: Maximum 2 kWh per trade
4. **Mock Fallback**: Falls back to rule-based decisions if AI server offline

## Testing

### Test AI Inference Server

```bash
curl -X POST http://localhost:5050/infer \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": "test_node",
    "telemetry": {
      "voltage": 230,
      "current": 3.5,
      "power": 805,
      "soc_kwh": 2.0,
      "pv_gen_kw": 1.2,
      "load_kw": 0.8
    }
  }'
```

### Test Backend AI Endpoint

```bash
curl -X POST http://localhost:3000/api/ai/infer/test_node \
  -H "Content-Type: application/json"
```

## Troubleshooting

### AI Server Not Starting
- Check Python dependencies are installed
- Verify policy pack path exists
- Check for model file (cql_policy.torchscript or cql_policy.onnx)

### Mock Decisions Being Used
- AI server may be offline - check health endpoint
- Start AI inference server
- Check AI_SERVER_URL environment variable

### Low Confidence Decisions
- Model may need retraining with more data
- Check observation values are realistic
- Verify normalization parameters match model

## Performance Considerations

- AI inference typically takes 10-50ms
- Health checks every 30 seconds
- Pending trade cleanup every 5 minutes
- Use batch inference for multiple nodes
