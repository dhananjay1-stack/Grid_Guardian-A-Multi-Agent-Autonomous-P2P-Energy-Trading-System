# Grid-Guardian AI Decision Engine

AI-powered decision engine for the Grid-Guardian energy trading system.

## Features

- **Dynamic Policy Selection**: Automatically selects BC/CQL/DT based on conditions
- **Condition Detection**: Risk, volatility, and system state assessment
- **Safety Shield**: Enforces SoC limits, power constraints, confidence thresholds
- **Edge Deployment**: Optimized for Raspberry Pi 5 (ARM64)

## Quick Start

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Run the server
python -m uvicorn app.main:app --host 0.0.0.0 --port 5050 --reload
```

### Docker

```bash
# Build image
docker build -t grid-guardian-ai .

# Run container
docker run -p 5050:5050 \
  -v /path/to/models:/app/models:ro \
  -v /path/to/policy_pack:/app/policy_pack:ro \
  grid-guardian-ai
```

### Docker Compose

```bash
docker-compose up -d
```

## API Endpoints

### POST /predict
Get forecasts without making decisions.

```bash
curl -X POST http://localhost:5050/predict \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": "node_1",
    "telemetry": {
      "soc_kwh": 2.0,
      "pv_gen_kw": 1.5,
      "load_kw": 0.8
    }
  }'
```

### POST /decide
Get trading/control decisions.

```bash
curl -X POST http://localhost:5050/decide \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": "node_1",
    "telemetry": {
      "soc_kwh": 2.0,
      "soc_capacity_kwh": 4.0,
      "pv_gen_kw": 1.5,
      "load_kw": 0.8,
      "price_signal": 0.15,
      "volatility": 0.1,
      "sensor_health": 0.98,
      "grid_risk": 0.05
    },
    "apply_safety": true
  }'
```

Response:
```json
{
  "node_id": "node_1",
  "action": "SELL",
  "energy": 0.56,
  "price": 0.15,
  "confidence": 0.87,
  "selected_model": "DT",
  "safety_status": "APPROVED",
  "condition_reason": "stable_condition_long_horizon_planning",
  "reason": "forecast_surplus_detected",
  "action_kw": -1.5,
  "trade_action": "SELL"
}
```

### GET /health
Service health check.

### GET /model/status
Model availability and metadata.

### GET /metrics
Performance metrics.

### POST /reload-model
Hot-reload a specific model.

## Policy Selection Logic

| Condition | Policy | Reason |
|-----------|--------|--------|
| Stable, normal operation | DT | Long-horizon planning |
| High volatility, grid risk | CQL | Conservative approach |
| Degraded sensors, stress test | BC | Safe fallback |

## Raspberry Pi Deployment

### Build ARM64 Image

```bash
# On Raspberry Pi or with cross-compilation
docker buildx build --platform linux/arm64 -t grid-guardian-ai:arm64 .
```

### Run on Raspberry Pi

```bash
docker run -d \
  --name grid-guardian-ai \
  -p 5050:5050 \
  --restart unless-stopped \
  --memory=1g \
  -v /home/pi/models:/app/models:ro \
  -v /home/pi/policy_pack:/app/policy_pack:ro \
  grid-guardian-ai:arm64
```

## Integration with Backend

The Node.js backend calls this service via HTTP:

```javascript
const response = await axios.post('http://localhost:5050/decide', {
  node_id: nodeId,
  telemetry: telemetryData,
  context: contextData,
  apply_safety: true
});

const { action, confidence, selected_model, safety_status } = response.data;
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| AI_SERVER_HOST | 0.0.0.0 | Server host |
| AI_SERVER_PORT | 5050 | Server port |
| AI_SERVER_DEBUG | false | Debug mode |
| DEFAULT_POLICY | DT | Default policy |
| FALLBACK_POLICY | BC | Fallback policy |
| USE_ONNX_RUNTIME | false | Use ONNX instead of TorchScript |
| LOG_LEVEL | INFO | Logging level |

## Architecture

```
Telemetry
    │
    ▼
┌─────────────────┐
│  Preprocessor   │ ─── Validate, normalize, derive features
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Condition       │ ─── Assess risk, volatility, sensor health
│ Detector        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Policy Router   │ ─── Select BC/CQL/DT based on conditions
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Model Inference │ ─── Run selected policy
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Safety Shield   │ ─── Enforce constraints, validate action
└────────┬────────┘
         │
         ▼
    Decision
```
