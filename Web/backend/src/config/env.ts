/**
 * Environment configuration
 */
import dotenv from 'dotenv';
import path from 'path';

// Load .env file
dotenv.config({ path: path.resolve(__dirname, '../../.env') });

export const config = {
  // Server
  port: parseInt(process.env.PORT || '3000', 10),
  nodeEnv: process.env.NODE_ENV || 'development',
  corsOrigins: process.env.CORS_ORIGINS?.split(',') || ['http://localhost:3001', 'http://localhost:5173'],

  // Database
  databaseUrl: process.env.DATABASE_URL || 'postgresql://grid:guardian@localhost:5432/grid_guardian',

  // Blockchain
  rpcUrl: process.env.RPC_URL || 'http://127.0.0.1:8545',
  chainId: parseInt(process.env.CHAIN_ID || '31337', 10),
  relayerPrivateKey: process.env.RELAYER_PRIVATE_KEY || '',

  // Contract addresses
  contracts: {
    identity: process.env.IDENTITY_SC_ADDRESS || '',
    collateral: process.env.COLLATERAL_SC_ADDRESS || '',
    trading: process.env.TRADING_SC_ADDRESS || '',
    matchRegistry: process.env.MATCH_REGISTRY_ADDRESS || '',
    settlement: process.env.SETTLEMENT_SC_ADDRESS || '',
    deliveryRegistry: process.env.DELIVERY_REGISTRY_ADDRESS || '',
  },

  // AI Model
  modelPath: process.env.MODEL_PATH || '../../Agentic_AI/edge/policy_pack/cql_policy.onnx',
  normParamsPath: process.env.NORM_PARAMS_PATH || '../../Agentic_AI/edge/policy_pack/norm_params.npz',

  // MQTT
  mqttBrokerUrl: process.env.MQTT_BROKER_URL || '',
  mqttUsername: process.env.MQTT_USERNAME || '',
  mqttPassword: process.env.MQTT_PASSWORD || '',

  // Auth
  jwtSecret: process.env.JWT_SECRET || 'change-this-in-production',
  jwtExpiresIn: process.env.JWT_EXPIRES_IN || '1d',

  // Rate limiting
  rateLimitWindowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS || '60000', 10),
  rateLimitMaxRequests: parseInt(process.env.RATE_LIMIT_MAX_REQUESTS || '100', 10),

  // Logging
  logLevel: process.env.LOG_LEVEL || 'debug',
};

// Validate required config
export function validateConfig(): void {
  const errors: string[] = [];

  if (!config.jwtSecret || config.jwtSecret === 'change-this-in-production') {
    if (config.nodeEnv === 'production') {
      errors.push('JWT_SECRET must be set in production');
    }
  }

  if (errors.length > 0) {
    throw new Error(`Configuration errors:\n${errors.join('\n')}`);
  }
}
