// Telemetry Types
export interface TelemetryData {
  node_id: string;
  voltage: number;
  current: number;
  power: number;
  timestamp: number;
  _id?: string;
}

export interface TelemetryHistory {
  node_id: string;
  data: TelemetryData[];
  period: '1h' | '24h';
}

// AI Decision Types
export type AIDecision = 'BUY' | 'SELL' | 'HOLD' | 'CHARGE' | 'DISCHARGE';
export type AIActionName = 'charge_small' | 'charge_large' | 'idle' | 'discharge_small' | 'discharge_large' | 'offer_sell' | 'offer_hold';
export type AITradeAction = 'BUY' | 'SELL' | null;
export type AISelectedModel = 'BC' | 'CQL' | 'DT' | 'FALLBACK' | 'ERROR' | string;
export type AICondition = 'stable' | 'uncertain' | 'risky' | 'degraded' | 'stress_test' | 'normal' | 'high_pv' | 'high_load' | 'low_soc' | 'peak_price' | 'off_peak' | 'fault' | string;

export interface AIDecisionData {
  node_id: string;
  decision: AIDecision;
  confidence: number;
  forecasted_load: number;
  forecasted_solar: number;
  recommended_quantity: number;
  timestamp: number;
  // Extended AI decision engine fields
  action_kw?: number;
  action_name?: AIActionName;
  trade_action?: AITradeAction;
  net_power_kw?: number;
  model_version?: string;
  is_mock?: boolean;
  execution_time_ms?: number;
  // Policy selection fields
  selected_model?: AISelectedModel;
  condition?: AICondition;
  condition_reason?: string;
  safety_status?: string;
  features?: {
    avg_power_24h?: number;
    peak_power?: number;
    soc_kwh?: number;
    soc_capacity_kwh?: number;
    grid_price?: number;
    [key: string]: number | undefined;
  };
}

export interface AITradeProposal {
  trade_id: string;
  node_id: string;
  trade_type: 'SELL' | 'BUY';
  quantity_kwh: number;
  confidence: number;
  ai_decision: AIDecision;
  net_power_kw: number;
  forecasted_load: number;
  forecasted_solar: number;
  timestamp: number;
  status: 'pending' | 'submitted' | 'executed' | 'failed' | 'cancelled';
  blockchain_result?: {
    success: boolean;
    tx_hash?: string;
    commit_hash?: string;
    round_id?: number;
    error?: string;
  };
}

export interface AIServerStatus {
  healthy: boolean;
  url: string;
  mockFallbackEnabled: boolean;
  modelInfo?: {
    model_name: string;
    obs_dim: number;
    act_dim: number;
    obs_keys: string[];
    metrics?: Record<string, number>;
    target_platform: string;
  };
  trading_integration?: {
    enabled: boolean;
    pending_trades: {
      total_pending: number;
      total_submitted: number;
      total_executed: number;
      total_failed: number;
    };
  };
}

export interface AIDecisionHistory {
  node_id: string;
  decisions: AIDecisionData[];
}

// Blockchain Trade Types
export type TradeStatus = 'PENDING' | 'MATCHED' | 'EXECUTED' | 'SETTLED' | 'DISPUTED' | 'FAILED' | 'CONFIRMED';
export type TradeType = 'BUY' | 'SELL';

export interface Trade {
  id: number;
  trade_id: string;
  node_id: string;
  trade_type: TradeType;
  energy_amount: number;
  price_per_unit: number;
  total_price: number;
  status: TradeStatus;
  counterparty_id?: string;
  blockchain_tx_hash?: string;
  created_at: string;
  updated_at: string;
}

export interface BlockchainEvent {
  id: string;
  event_type: string;
  tx_hash?: string;
  node_id: string;
  payload: Record<string, unknown>;
  timestamp: number;
  status: string;
}

// System Types
export interface SystemHealth {
  status: 'OK' | 'DEGRADED' | 'ERROR';
  timestamp: number;
  uptime: number;
  services: {
    mongodb: { status: string; readyState?: number };
    postgresql: { status: string };
    mqtt: { status: string; broker?: string; reconnectAttempts?: number };
  };
  memory: {
    heapUsed: string;
    heapTotal: string;
    external: string;
    rss: string;
  };
  environment: string;
  nodeVersion: string;
}

// Node Types
export interface Node {
  node_id: string;
  status: 'ACTIVE' | 'IDLE' | 'OFFLINE' | 'ERROR';
  latest_telemetry?: TelemetryData;
  latest_ai_decision?: AIDecisionData;
  alerts: Alert[];
  last_seen: number;
}

export interface Alert {
  id: string;
  node_id: string;
  type: 'overvoltage' | 'undervoltage' | 'overcurrent' | 'overpower' | 'communication' | 'general';
  message: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  timestamp: number;
  acknowledged: boolean;
}

// Dashboard Summary Types
export interface DashboardSummary {
  total_nodes: number;
  active_nodes: number;
  total_power: number;
  avg_voltage: number;
  pending_trades: number;
  latest_ai_decision?: AIDecision;
  system_status: 'OK' | 'DEGRADED' | 'ERROR';
  nodes: NodeSummary[];
}

export interface NodeSummary {
  node_id: string;
  latest_power: number;
  status: 'ACTIVE' | 'IDLE' | 'OFFLINE' | 'ERROR';
  alerts: Alert[];
  ai_decision?: AIDecision;
}

// Control Types
export interface ControlState {
  trading_enabled: boolean;
  manual_override: boolean;
  safe_mode: boolean;
  last_action?: {
    action: string;
    timestamp: number;
    success: boolean;
  };
}

// API Response Types
export interface ApiResponse<T> {
  success: boolean;
  data: T;
  error?: string;
  message?: string;
}

// WebSocket Event Types
export interface WSEvent<T = unknown> {
  type: string;
  payload: T;
  timestamp: number;
}
