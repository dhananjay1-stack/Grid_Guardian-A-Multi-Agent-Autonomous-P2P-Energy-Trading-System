/**
 * AI Decision Engine Configuration
 *
 * Configuration for the AI inference server and decision engine integration.
 */

const path = require('path');

module.exports = {
  // AI inference server settings
  server: {
    url: process.env.AI_SERVER_URL || 'http://127.0.0.1:5050',
    timeout: parseInt(process.env.AI_SERVER_TIMEOUT) || 5000,
    healthCheckInterval: parseInt(process.env.AI_HEALTH_CHECK_INTERVAL) || 30000,
  },

  // Fallback behavior
  fallback: {
    useMock: process.env.AI_USE_MOCK_FALLBACK !== 'false',
    mockModelVersion: 'mock-v1.0.0',
  },

  // Model paths for direct inference (if running in same process)
  model: {
    policyPackPath: process.env.POLICY_PACK_PATH ||
      path.join(__dirname, '..', '..', '..', 'Agentic_AI', 'edge', 'policy_pack'),
    torchscriptModel: 'cql_policy.torchscript',
    onnxModel: 'cql_policy.onnx',
    normParams: 'norm_params.npz',
    modelCard: 'model_card.json',
  },

  // Observation configuration
  observation: {
    // Default values for missing telemetry fields
    defaults: {
      soc_kwh: 2.0,
      soc_capacity_kwh: 4.0,
      pv_gen_kw: 0.5,
      load_kw: 0.8,
      net_kw: 0,
      battery_power_kw: 0,
      price_signal: 0.15,
      forecast_irradiance_1h: 400,
      forecast_irradiance_3h: 350,
      forecast_temp_1h: 25,
      actual_irradiance_wm2: 450,
      voltage_v: 230,
      current_a: 3.5,
    },

    // Observation keys expected by model (18-dimensional)
    keys: [
      'soc_kwh',
      'soc_capacity_kwh',
      'pv_gen_kw',
      'load_kw',
      'net_kw',
      'battery_power_kw',
      'price_signal',
      'forecast_irradiance_1h',
      'forecast_irradiance_3h',
      'forecast_temp_1h',
      'actual_irradiance_wm2',
      'voltage_v',
      'current_a',
    ],
  },

  // Action mapping (matches edge_inference.py)
  actions: {
    0: { name: 'charge_small', kw: 1.0, decision: 'CHARGE', trade: null },
    1: { name: 'charge_large', kw: 3.0, decision: 'CHARGE', trade: null },
    2: { name: 'idle', kw: 0.0, decision: 'HOLD', trade: null },
    3: { name: 'discharge_small', kw: -1.0, decision: 'DISCHARGE', trade: null },
    4: { name: 'discharge_large', kw: -3.0, decision: 'DISCHARGE', trade: null },
    5: { name: 'offer_sell', kw: -1.5, decision: 'SELL', trade: 'SELL' },
    6: { name: 'offer_hold', kw: 0.0, decision: 'HOLD', trade: null },
  },

  // Safety constraints
  safety: {
    socMinFrac: 0.10, // Minimum SoC as fraction of capacity
    socMaxFrac: 0.95, // Maximum SoC as fraction of capacity
    maxChargeKw: 3.0, // Maximum charge rate
    maxDischargeKw: 3.0, // Maximum discharge rate
    intervalMinutes: 5, // Decision interval in minutes
  },

  // Trading thresholds
  trading: {
    // Minimum confidence to trigger trade
    minConfidence: 0.6,
    // Maximum quantity per trade (kWh)
    maxQuantity: 2.0,
    // Surplus fraction to offer for sale
    surplusSellFraction: 0.8,
    // Minimum net power (kW) to consider selling
    minNetPowerForSale: 0.5,
  },

  // Real-time streaming configuration
  streaming: {
    // Emit decisions via Socket.io
    emitToSocketio: true,
    // Publish to MQTT
    publishToMqtt: true,
    // Topic prefix for MQTT
    mqttTopicPrefix: 'gridguardian',
  },

  // Logging configuration
  logging: {
    logDecisions: true,
    logInferenceTime: true,
    logConfidenceThreshold: 0.5, // Log decisions below this confidence as warnings
  },
};
