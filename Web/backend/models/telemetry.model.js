const mongoose = require('mongoose');

const telemetrySchema = new mongoose.Schema(
  {
    node_id: {
      type: String,
      required: true,
      index: true,
    },
    voltage: {
      type: Number,
      required: true,
      min: 0,
    },
    current: {
      type: Number,
      required: true,
      min: 0,
    },
    power: {
      type: Number,
      required: true,
      min: 0,
    },
    timestamp: {
      type: Number,
      required: true,
    },
    source: {
      type: String,
      enum: ['MQTT', 'REST'],
      default: 'MQTT',
    },
    status: {
      type: String,
      enum: ['ACTIVE', 'INACTIVE', 'ERROR'],
      default: 'ACTIVE',
    },
  },
  {
    timestamps: true,
    collection: 'telemetry',
  }
);

// Compound index for efficient queries
telemetrySchema.index({ node_id: 1, timestamp: -1 });
telemetrySchema.index({ createdAt: -1 });

// Static method to get latest telemetry for a node
telemetrySchema.statics.getLatestByNodeId = async function (nodeId) {
  return this.findOne({ node_id: nodeId }).sort({ timestamp: -1 }).lean();
};

// Static method to get history with pagination
telemetrySchema.statics.getHistoryByNodeId = async function (nodeId, limit = 100, skip = 0) {
  return this.find({ node_id: nodeId })
    .sort({ timestamp: -1 })
    .skip(skip)
    .limit(limit)
    .lean();
};

module.exports = mongoose.model('Telemetry', telemetrySchema);
