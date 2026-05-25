/**
 * TypeScript type definitions for Grid-Guardian Backend
 */

// ═══════════════════════════════════════════════════════════════════
//                        API TYPES
// ═══════════════════════════════════════════════════════════════════

export interface ApiResponse<T = unknown> {
  success?: boolean;
  data?: T;
  error?: string;
  message?: string;
  timestamp?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    pages: number;
  };
}

// ═══════════════════════════════════════════════════════════════════
//                        NODE TYPES
// ═══════════════════════════════════════════════════════════════════

export interface NodeStatus {
  nodeId: string;
  status: 'pending' | 'active' | 'attested' | 'revoked' | 'offline';
  lastSeen?: Date;
  deposit?: string;
  stake?: string;
}

export interface NodeOnChainData {
  owner: string;
  pubkeyHash: string;
  metaURI: string;
  stake: string;
  registeredAt: number;
  active: boolean;
  attested: boolean;
}

export interface NodeTelemetry {
  nodeId: string;
  timestamp: Date;
  soc_kwh: number;
  soc_capacity_kwh: number;
  pv_gen_kw: number;
  load_kw: number;
  net_kw: number;
  battery_power_kw: number;
  price_signal: number;
  voltage_v?: number;
  current_a?: number;
  action_taken?: number;
}

// ═══════════════════════════════════════════════════════════════════
//                        TRADE TYPES
// ═══════════════════════════════════════════════════════════════════

export enum TradeStatus {
  None = 0,
  Locked = 1,
  Delivered = 2,
  Settled = 3,
  Disputed = 4,
  Refunded = 5,
}

export interface TradeOnChainData {
  matchHash: string;
  buyerNodeId: string;
  sellerNodeId: string;
  kwhBucket: number;
  priceBucket: number;
  lockedAmount: string;
  status: TradeStatus;
  proposedBlock: number;
  deliveredBlock: number;
}

export interface TradeEvent {
  type: 'proposed' | 'executed' | 'delivered' | 'settled' | 'disputed' | 'refunded';
  tradeId: string;
  timestamp: Date;
  txHash?: string;
  details?: Record<string, unknown>;
}

// ═══════════════════════════════════════════════════════════════════
//                        INFERENCE TYPES
// ═══════════════════════════════════════════════════════════════════

export interface Observation {
  soc_kwh: number;
  soc_capacity_kwh: number;
  pv_gen_kw: number;
  load_kw: number;
  net_kw?: number;
  battery_power_kw?: number;
  price_signal?: number;
  forecast_irradiance_1h?: number;
  forecast_irradiance_3h?: number;
  forecast_temp_1h?: number;
  actual_irradiance_wm2?: number;
  voltage_v?: number;
  current_a?: number;
}

export interface InferenceResult {
  action_index: number;
  action_name: string;
  action_kw: number;
  logits: number[];
  safety_applied: boolean;
  original_kw?: number;
  inference_time_ms?: number;
}

export interface ModelInfo {
  model_name: string;
  obs_dim: number;
  act_dim: number;
  obs_keys: string[];
  normalization: string;
  target_platform: string;
  metrics?: Record<string, number>;
}

// ═══════════════════════════════════════════════════════════════════
//                        BLOCKCHAIN TYPES
// ═══════════════════════════════════════════════════════════════════

export interface BlockchainStatus {
  connected: boolean;
  chainId: number;
  blockNumber: number;
  relayerAddress: string;
  relayerBalance: string;
}

export interface ContractAddresses {
  identity: string;
  collateral: string;
  trading: string;
  matchRegistry: string;
  settlement: string;
  deliveryRegistry: string;
}

// ═══════════════════════════════════════════════════════════════════
//                        WEBSOCKET TYPES
// ═══════════════════════════════════════════════════════════════════

export interface WSMessage {
  type: string;
  payload?: unknown;
  timestamp?: string;
}

export interface TelemetryUpdateMessage {
  type: 'telemetry:update';
  payload: {
    nodeId: string;
    data: NodeTelemetry;
    timestamp: string;
  };
}

export interface TradeEventMessage {
  type: `trade:${'proposed' | 'executed' | 'delivered' | 'settled'}`;
  payload: {
    tradeId: string;
    [key: string]: unknown;
  };
}

export interface NodeStatusMessage {
  type: 'node:status';
  payload: {
    nodeId: string;
    status: string;
  };
}

export interface AlertMessage {
  type: 'alert:new';
  payload: {
    type: string;
    message: string;
    severity: 'info' | 'warning' | 'error' | 'critical';
  };
}

// ═══════════════════════════════════════════════════════════════════
//                        RELAYER TYPES
// ═══════════════════════════════════════════════════════════════════

export interface RegisterNodeRequest {
  nodeId: string;
  pubkeyHash: string;
  metaURI: string;
  nonce: number;
  expiry: number;
  signature: string;
  signer: string;
}

export interface DeliveryReceiptRequest {
  tradeId: string;
  nodeId: string;
  meterSnapshotHash: string;
  deliveredKwhBucket: number;
  periodStart: number;
  periodEnd: number;
  nonce: number;
  signature: string;
  signer: string;
}

export interface GasVoucher {
  nodeId: string;
  relayer: string;
  amount: string;
  maxGas: number;
  gasPrice: string;
  nonce: number;
  expiry: number;
  txHash: string;
}
