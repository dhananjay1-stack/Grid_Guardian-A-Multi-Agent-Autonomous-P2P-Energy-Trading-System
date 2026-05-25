const mongoose = require('mongoose');

const blockchainLogSchema = new mongoose.Schema(
  {
    node_id: {
      type: String,
      required: true,
      index: true,
    },
    event_type: {
      type: String,
      required: true,
    },
    tx_hash: {
      type: String,
      sparse: true,
    },
    block_number: {
      type: Number,
      sparse: true,
    },
    log_index: {
      type: Number,
      sparse: true,
    },
    payload: {
      type: mongoose.Schema.Types.Mixed,
      default: {},
    },
    status: {
      type: String,
      enum: ['PENDING', 'CONFIRMED', 'FAILED'],
      default: 'PENDING',
    },
    gas_used: {
      type: Number,
      default: 0,
    },
    logged_at: {
      type: Date,
      default: Date.now,
    },
  },
  {
    timestamps: true,
    collection: 'blockchain_logs',
  }
);

blockchainLogSchema.index({ node_id: 1, createdAt: -1 });
blockchainLogSchema.index({ tx_hash: 1 }, { sparse: true });
blockchainLogSchema.index({ event_type: 1, createdAt: -1 });
blockchainLogSchema.index(
  { tx_hash: 1, log_index: 1, event_type: 1 },
  {
    unique: true,
    sparse: true,
    partialFilterExpression: { tx_hash: { $type: 'string' }, log_index: { $type: 'number' } },
  }
);

// Get recent logs for a node (or all nodes if nodeId is null)
blockchainLogSchema.statics.getRecentLogs = async function (nodeId, limit = 50) {
  const query = nodeId ? { node_id: nodeId } : {};
  return this.find(query).sort({ createdAt: -1 }).limit(limit).lean();
};

module.exports = mongoose.model('BlockchainLog', blockchainLogSchema);
