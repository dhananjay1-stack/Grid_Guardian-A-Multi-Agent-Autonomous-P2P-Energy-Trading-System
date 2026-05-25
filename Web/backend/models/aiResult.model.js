const mongoose = require('mongoose');

const aiResultSchema = new mongoose.Schema(
  {
    node_id: {
      type: String,
      required: true,
      index: true,
    },
    decision: {
      type: String,
      enum: ['SELL', 'BUY', 'HOLD', 'STORE', 'CHARGE', 'DISCHARGE'],
      required: true,
    },
    confidence: {
      type: Number,
      min: 0,
      max: 1,
      default: 0.5,
    },
    features: {
      voltage: Number,
      current: Number,
      power: Number,
      timestamp: Number,
      avg_power_24h: Number,
      peak_power: Number,
      grid_price: Number,
      soc_kwh: Number,
      soc_capacity_kwh: Number,
    },
    model_version: {
      type: String,
      default: 'GridGuardian-CQL',
    },
    processed_at: {
      type: Date,
      default: Date.now,
    },
    execution_time_ms: {
      type: Number,
      default: 0,
    },
    // Extended fields for AI decision engine
    action_kw: {
      type: Number,
      default: 0,
    },
    action_name: {
      type: String,
      default: 'idle',
    },
    trade_action: {
      type: String,
      enum: ['SELL', 'BUY', null],
      default: null,
    },
    recommended_quantity: {
      type: Number,
      default: 0,
    },
    forecasted_load: {
      type: Number,
      default: 0,
    },
    forecasted_solar: {
      type: Number,
      default: 0,
    },
    net_power_kw: {
      type: Number,
      default: 0,
    },
    is_mock: {
      type: Boolean,
      default: false,
    },
    // Policy selection fields
    selected_model: {
      type: String,
      enum: ['BC', 'CQL', 'DT', 'FALLBACK', 'RULE_BASED', 'ERROR', null],
      default: null,
    },
    condition: {
      type: String,
      default: null,
    },
    condition_reason: {
      type: String,
      default: null,
    },
    condition_confidence: {
      type: Number,
      min: 0,
      max: 1,
      default: null,
    },
    volatility: {
      type: Number,
      default: null,
    },
    sub_conditions: {
      type: [String],
      default: [],
    },
    safety_status: {
      type: String,
      default: null,
    },
  },
  {
    timestamps: true,
    collection: 'ai_results',
  }
);

aiResultSchema.index({ node_id: 1, createdAt: -1 });

// Get latest AI decision for a node
aiResultSchema.statics.getLatestDecision = async function (nodeId) {
  return this.findOne({ node_id: nodeId }).sort({ createdAt: -1 }).lean();
};

module.exports = mongoose.model('AIResult', aiResultSchema);
