/**
 * Sequelize Models Index
 */
import { DataTypes, Model, Optional } from 'sequelize';
import { sequelize } from '../config/database';

// ═══════════════════════════════════════════════════════════════════
//                        NODE MODEL
// ═══════════════════════════════════════════════════════════════════

interface NodeAttributes {
  id: number;
  nodeId: string;
  ownerAddress: string;
  pubkeyHash: string;
  metaUri: string | null;
  archetype: string;
  status: 'pending' | 'active' | 'attested' | 'revoked';
  registeredAt: Date;
  attestedAt: Date | null;
  lastSeen: Date | null;
  metadata: Record<string, unknown> | null;
}

interface NodeCreationAttributes extends Optional<NodeAttributes, 'id' | 'metaUri' | 'archetype' | 'status' | 'registeredAt' | 'attestedAt' | 'lastSeen' | 'metadata'> {}

export class Node extends Model<NodeAttributes, NodeCreationAttributes> implements NodeAttributes {
  public id!: number;
  public nodeId!: string;
  public ownerAddress!: string;
  public pubkeyHash!: string;
  public metaUri!: string | null;
  public archetype!: string;
  public status!: 'pending' | 'active' | 'attested' | 'revoked';
  public registeredAt!: Date;
  public attestedAt!: Date | null;
  public lastSeen!: Date | null;
  public metadata!: Record<string, unknown> | null;
}

Node.init({
  id: { type: DataTypes.INTEGER, autoIncrement: true, primaryKey: true },
  nodeId: { type: DataTypes.STRING(66), allowNull: false, unique: true, field: 'node_id' },
  ownerAddress: { type: DataTypes.STRING(42), allowNull: false, field: 'owner_address' },
  pubkeyHash: { type: DataTypes.STRING(66), allowNull: false, field: 'pubkey_hash' },
  metaUri: { type: DataTypes.TEXT, field: 'meta_uri' },
  archetype: { type: DataTypes.STRING(50), defaultValue: 'prosumer' },
  status: { type: DataTypes.ENUM('pending', 'active', 'attested', 'revoked'), defaultValue: 'pending' },
  registeredAt: { type: DataTypes.DATE, defaultValue: DataTypes.NOW, field: 'registered_at' },
  attestedAt: { type: DataTypes.DATE, field: 'attested_at' },
  lastSeen: { type: DataTypes.DATE, field: 'last_seen' },
  metadata: { type: DataTypes.JSONB },
}, {
  sequelize,
  tableName: 'nodes',
  timestamps: false,
});

// ═══════════════════════════════════════════════════════════════════
//                        TRADE MODEL
// ═══════════════════════════════════════════════════════════════════

interface TradeAttributes {
  id: number;
  tradeId: string;
  matchHash: string | null;
  buyerNodeId: string;
  sellerNodeId: string;
  kwhBucket: number | null;
  priceBucket: number | null;
  lockedAmount: string | null;
  status: 'proposed' | 'locked' | 'delivered' | 'settled' | 'disputed' | 'refunded';
  proposedAt: Date;
  deliveredAt: Date | null;
  settledAt: Date | null;
  txHash: string | null;
  metadata: Record<string, unknown> | null;
}

interface TradeCreationAttributes extends Optional<TradeAttributes, 'id' | 'matchHash' | 'kwhBucket' | 'priceBucket' | 'lockedAmount' | 'status' | 'proposedAt' | 'deliveredAt' | 'settledAt' | 'txHash' | 'metadata'> {}

export class Trade extends Model<TradeAttributes, TradeCreationAttributes> implements TradeAttributes {
  public id!: number;
  public tradeId!: string;
  public matchHash!: string | null;
  public buyerNodeId!: string;
  public sellerNodeId!: string;
  public kwhBucket!: number | null;
  public priceBucket!: number | null;
  public lockedAmount!: string | null;
  public status!: 'proposed' | 'locked' | 'delivered' | 'settled' | 'disputed' | 'refunded';
  public proposedAt!: Date;
  public deliveredAt!: Date | null;
  public settledAt!: Date | null;
  public txHash!: string | null;
  public metadata!: Record<string, unknown> | null;
}

Trade.init({
  id: { type: DataTypes.INTEGER, autoIncrement: true, primaryKey: true },
  tradeId: { type: DataTypes.STRING(66), allowNull: false, unique: true, field: 'trade_id' },
  matchHash: { type: DataTypes.STRING(66), field: 'match_hash' },
  buyerNodeId: { type: DataTypes.STRING(66), allowNull: false, field: 'buyer_node_id' },
  sellerNodeId: { type: DataTypes.STRING(66), allowNull: false, field: 'seller_node_id' },
  kwhBucket: { type: DataTypes.INTEGER, field: 'kwh_bucket' },
  priceBucket: { type: DataTypes.INTEGER, field: 'price_bucket' },
  lockedAmount: { type: DataTypes.DECIMAL(78), field: 'locked_amount' },
  status: {
    type: DataTypes.ENUM('proposed', 'locked', 'delivered', 'settled', 'disputed', 'refunded'),
    defaultValue: 'proposed',
  },
  proposedAt: { type: DataTypes.DATE, defaultValue: DataTypes.NOW, field: 'proposed_at' },
  deliveredAt: { type: DataTypes.DATE, field: 'delivered_at' },
  settledAt: { type: DataTypes.DATE, field: 'settled_at' },
  txHash: { type: DataTypes.STRING(66), field: 'tx_hash' },
  metadata: { type: DataTypes.JSONB },
}, {
  sequelize,
  tableName: 'trades',
  timestamps: false,
});

// ═══════════════════════════════════════════════════════════════════
//                        TELEMETRY MODEL
// ═══════════════════════════════════════════════════════════════════

interface TelemetryAttributes {
  id: number;
  nodeId: string;
  timestamp: Date;
  socKwh: number | null;
  socCapacityKwh: number | null;
  pvGenKw: number | null;
  loadKw: number | null;
  netKw: number | null;
  batteryPowerKw: number | null;
  priceSignal: number | null;
  voltageV: number | null;
  currentA: number | null;
  actionTaken: number | null;
  rawData: Record<string, unknown> | null;
}

interface TelemetryCreationAttributes extends Optional<TelemetryAttributes, 'id' | 'socKwh' | 'socCapacityKwh' | 'pvGenKw' | 'loadKw' | 'netKw' | 'batteryPowerKw' | 'priceSignal' | 'voltageV' | 'currentA' | 'actionTaken' | 'rawData'> {}

export class Telemetry extends Model<TelemetryAttributes, TelemetryCreationAttributes> implements TelemetryAttributes {
  public id!: number;
  public nodeId!: string;
  public timestamp!: Date;
  public socKwh!: number | null;
  public socCapacityKwh!: number | null;
  public pvGenKw!: number | null;
  public loadKw!: number | null;
  public netKw!: number | null;
  public batteryPowerKw!: number | null;
  public priceSignal!: number | null;
  public voltageV!: number | null;
  public currentA!: number | null;
  public actionTaken!: number | null;
  public rawData!: Record<string, unknown> | null;
}

Telemetry.init({
  id: { type: DataTypes.INTEGER, autoIncrement: true, primaryKey: true },
  nodeId: { type: DataTypes.STRING(66), allowNull: false, field: 'node_id' },
  timestamp: { type: DataTypes.DATE, allowNull: false },
  socKwh: { type: DataTypes.REAL, field: 'soc_kwh' },
  socCapacityKwh: { type: DataTypes.REAL, field: 'soc_capacity_kwh' },
  pvGenKw: { type: DataTypes.REAL, field: 'pv_gen_kw' },
  loadKw: { type: DataTypes.REAL, field: 'load_kw' },
  netKw: { type: DataTypes.REAL, field: 'net_kw' },
  batteryPowerKw: { type: DataTypes.REAL, field: 'battery_power_kw' },
  priceSignal: { type: DataTypes.REAL, field: 'price_signal' },
  voltageV: { type: DataTypes.REAL, field: 'voltage_v' },
  currentA: { type: DataTypes.REAL, field: 'current_a' },
  actionTaken: { type: DataTypes.INTEGER, field: 'action_taken' },
  rawData: { type: DataTypes.JSONB, field: 'raw_data' },
}, {
  sequelize,
  tableName: 'telemetry',
  timestamps: false,
  indexes: [
    { fields: ['node_id', 'timestamp'] },
  ],
});

// ═══════════════════════════════════════════════════════════════════
//                        AUDIT LOG MODEL
// ═══════════════════════════════════════════════════════════════════

interface AuditLogAttributes {
  id: number;
  timestamp: Date;
  eventType: string;
  actorType: 'user' | 'node' | 'system' | null;
  actorId: string | null;
  resourceType: string | null;
  resourceId: string | null;
  action: string | null;
  details: Record<string, unknown> | null;
  ipAddress: string | null;
}

interface AuditLogCreationAttributes extends Optional<AuditLogAttributes, 'id' | 'timestamp' | 'actorType' | 'actorId' | 'resourceType' | 'resourceId' | 'action' | 'details' | 'ipAddress'> {}

export class AuditLog extends Model<AuditLogAttributes, AuditLogCreationAttributes> implements AuditLogAttributes {
  public id!: number;
  public timestamp!: Date;
  public eventType!: string;
  public actorType!: 'user' | 'node' | 'system' | null;
  public actorId!: string | null;
  public resourceType!: string | null;
  public resourceId!: string | null;
  public action!: string | null;
  public details!: Record<string, unknown> | null;
  public ipAddress!: string | null;
}

AuditLog.init({
  id: { type: DataTypes.INTEGER, autoIncrement: true, primaryKey: true },
  timestamp: { type: DataTypes.DATE, defaultValue: DataTypes.NOW },
  eventType: { type: DataTypes.STRING(50), allowNull: false, field: 'event_type' },
  actorType: { type: DataTypes.ENUM('user', 'node', 'system'), field: 'actor_type' },
  actorId: { type: DataTypes.STRING(66), field: 'actor_id' },
  resourceType: { type: DataTypes.STRING(50), field: 'resource_type' },
  resourceId: { type: DataTypes.STRING(66), field: 'resource_id' },
  action: { type: DataTypes.STRING(50) },
  details: { type: DataTypes.JSONB },
  ipAddress: { type: DataTypes.STRING(45), field: 'ip_address' },
}, {
  sequelize,
  tableName: 'audit_logs',
  timestamps: false,
});

// ═══════════════════════════════════════════════════════════════════
//                        USER MODEL
// ═══════════════════════════════════════════════════════════════════

interface UserAttributes {
  id: number;
  email: string;
  passwordHash: string;
  role: 'admin' | 'operator' | 'viewer';
  createdAt: Date;
  lastLogin: Date | null;
}

interface UserCreationAttributes extends Optional<UserAttributes, 'id' | 'role' | 'createdAt' | 'lastLogin'> {}

export class User extends Model<UserAttributes, UserCreationAttributes> implements UserAttributes {
  public id!: number;
  public email!: string;
  public passwordHash!: string;
  public role!: 'admin' | 'operator' | 'viewer';
  public createdAt!: Date;
  public lastLogin!: Date | null;
}

User.init({
  id: { type: DataTypes.INTEGER, autoIncrement: true, primaryKey: true },
  email: { type: DataTypes.STRING(255), allowNull: false, unique: true },
  passwordHash: { type: DataTypes.STRING(255), allowNull: false, field: 'password_hash' },
  role: { type: DataTypes.ENUM('admin', 'operator', 'viewer'), defaultValue: 'viewer' },
  createdAt: { type: DataTypes.DATE, defaultValue: DataTypes.NOW, field: 'created_at' },
  lastLogin: { type: DataTypes.DATE, field: 'last_login' },
}, {
  sequelize,
  tableName: 'users',
  timestamps: false,
});

export { sequelize };
