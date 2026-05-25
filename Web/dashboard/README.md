# Grid-Guardian Dashboard

Production-ready React dashboard for the Grid-Guardian AI + Blockchain + Edge energy trading system.

## Features

- **Real-Time Monitoring** - Live telemetry updates via Socket.io
- **AI Decision Panel** - View AI trading decisions with confidence scores
- **Blockchain Trades** - Track trade lifecycle and settlement status
- **Control Panel** - Enable/disable trading, manual override, safe mode
- **Responsive Design** - Works on desktop and tablet
- **Dark Theme** - Modern, professional control-room style UI

## Tech Stack

- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS
- **State Management**: Zustand
- **Charts**: Recharts
- **Real-time**: Socket.io Client
- **HTTP Client**: Axios
- **Icons**: Lucide React

## Project Structure

```
dashboard/
├── src/
│   ├── app/
│   │   ├── globals.css      # Tailwind + custom styles
│   │   ├── layout.tsx       # Root layout
│   │   └── page.tsx         # Main dashboard page
│   ├── components/
│   │   ├── dashboard/
│   │   │   ├── TelemetryCard.tsx
│   │   │   ├── TelemetryChart.tsx
│   │   │   ├── AIDecisionCard.tsx
│   │   │   ├── ForecastPanel.tsx
│   │   │   ├── TradePanel.tsx
│   │   │   ├── ControlPanel.tsx
│   │   │   ├── HealthStatusCard.tsx
│   │   │   ├── LiveConnectionBadge.tsx
│   │   │   └── SummaryStrip.tsx
│   │   └── ui/
│   │       ├── card.tsx
│   │       ├── button.tsx
│   │       ├── badge.tsx
│   │       └── skeleton.tsx
│   ├── services/
│   │   ├── api.ts           # Axios client
│   │   ├── telemetry.service.ts
│   │   ├── ai.service.ts
│   │   ├── blockchain.service.ts
│   │   ├── control.service.ts
│   │   └── realtime.service.ts
│   ├── store/
│   │   ├── telemetry.store.ts
│   │   ├── ai.store.ts
│   │   ├── blockchain.store.ts
│   │   ├── system.store.ts
│   │   └── control.store.ts
│   ├── hooks/
│   │   └── useDashboardData.ts
│   ├── types/
│   │   └── index.ts
│   └── lib/
│       └── utils.ts
├── public/
├── package.json
├── tailwind.config.js
├── tsconfig.json
└── next.config.js
```

## Installation

```bash
cd dashboard

# Install dependencies
npm install
```

## Configuration

Create `.env.local` file:

```env
NEXT_PUBLIC_API_URL=http://localhost:3000
NEXT_PUBLIC_WS_URL=http://localhost:3000
```

## Running

```bash
# Development mode
npm run dev

# Production build
npm run build
npm start
```

The dashboard will be available at `http://localhost:3001`

## API Requirements

The dashboard expects the backend to provide these endpoints:

### Required Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/system/health` | GET | System health status |
| `/api/dashboard/summary` | GET | Dashboard summary data |
| `/api/telemetry/latest/:node_id` | GET | Latest telemetry |
| `/api/telemetry/history/:node_id` | GET | Telemetry history |
| `/api/ai/decision/:node_id` | GET | AI decision for node |
| `/api/blockchain/trades` | GET | List of trades |

### Control Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/control/trading/enable` | POST | Enable trading |
| `/api/control/trading/disable` | POST | Disable trading |
| `/api/control/manual-override` | POST | Toggle manual override |
| `/api/control/safe-mode/enable` | POST | Enable safe mode |
| `/api/control/safe-mode/disable` | POST | Disable safe mode |
| `/api/system/refresh` | POST | Refresh system data |

### Socket.io Events

The dashboard listens for:
- `telemetry` - Real-time telemetry updates
- `telemetry:node` - Node-specific telemetry
- `ai:decision` - AI decision updates
- `blockchain:trade` - Trade status updates
- `alert` - System alerts
- `status` - Node status updates

## Testing

### 1. Start Backend

```bash
cd ../backend
npm run dev
```

### 2. Start Dashboard

```bash
cd ../dashboard
npm run dev
```

### 3. Test Real-time Updates

Submit telemetry via MQTT or REST API:

```bash
# REST API
curl -X POST http://localhost:3000/api/telemetry \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": "pi-001",
    "voltage": 230,
    "current": 5.2,
    "power": 1196,
    "timestamp": '$(date +%s)'
  }'
```

### 4. Test Control Panel

1. Click "Disable" in Control Panel
2. Confirm the action
3. Verify status badge changes to "Trading OFF"
4. Click "Enable" to re-enable trading

### 5. Verify Error Handling

1. Stop the backend server
2. Verify the dashboard shows "Disconnected" status
3. Verify error states are displayed gracefully
4. Restart backend and verify reconnection

## Dashboard Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Grid-Guardian                              [Live] [Refresh]    │
├─────────────────────────────────────────────────────────────────┤
│  [System OK] [Nodes 1/1] [Power 1.2kW] [Voltage 230V] [Trades 2]│
├─────────────────┬──────────────────┬────────────────────────────┤
│                 │                  │                            │
│  TELEMETRY      │  AI DECISION     │  BLOCKCHAIN TRADES         │
│  ┌───────────┐  │  ┌────────────┐  │  ┌────────────────────┐    │
│  │ pi-001    │  │  │   SELL     │  │  │ TRADE-ABC123       │    │
│  │ 230V      │  │  │ 85% conf   │  │  │ Status: CONFIRMED  │    │
│  │ 5.2A      │  │  └────────────┘  │  │ 0.5 kWh @ $0.15    │    │
│  │ 1.2kW     │  │                  │  └────────────────────┘    │
│  └───────────┘  │  FORECASTS       │  ┌────────────────────┐    │
│                 │  ┌────────────┐  │  │ TRADE-DEF456       │    │
│  [CHART]        │  │ Load: 1kW  │  │  │ Status: PENDING    │    │
│                 │  │ Solar: 2kW │  │  └────────────────────┘    │
│                 │  │ Net: +1kW  │  │                            │
│                 │  └────────────┘  │                            │
├─────────────────┴──────────────────┴────────────────────────────┤
│  CONTROL PANEL               │  SYSTEM HEALTH                   │
│  [Enable] [Disable]          │  MongoDB: ● Connected            │
│  [Manual Override]           │  PostgreSQL: ● Connected         │
│  [Safe Mode]                 │  MQTT: ● Connected               │
│  [Refresh]                   │  Memory: 45MB | Uptime: 2h       │
└──────────────────────────────┴──────────────────────────────────┘
```

## Customization

### Adding New Nodes

The dashboard supports multiple nodes. Modify `page.tsx` to add node selection:

```tsx
const nodes = ['pi-001', 'pi-002', 'pi-003'];
const [selectedNode, setSelectedNode] = useState(nodes[0]);
```

### Changing Theme

Edit `src/app/globals.css` to modify CSS variables for light/dark themes.

### Adding New Metrics

1. Add type definition in `src/types/index.ts`
2. Update relevant store in `src/store/`
3. Create new component in `src/components/dashboard/`
4. Add to main dashboard page
