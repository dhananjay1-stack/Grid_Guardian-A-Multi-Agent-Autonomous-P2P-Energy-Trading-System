const mongoose = require('mongoose');

const txLogSchema = new mongoose.Schema(
  {
    operation: {
      type: String,
      required: true,
      index: true,
    },
    contract_address: {
      type: String,
      required: true,
      index: true,
    },
    idempotency_key: {
      type: String,
      required: true,
      index: true,
    },
    method: {
      type: String,
      required: true,
    },
    args: {
      type: mongoose.Schema.Types.Mixed,
      default: [],
    },
    tx_hash: {
      type: String,
      sparse: true,
      index: true,
    },
    status: {
      type: String,
      enum: ['PENDING', 'CONFIRMED', 'FAILED'],
      required: true,
      default: 'PENDING',
    },
    attempt: {
      type: Number,
      default: 1,
    },
    receipt: {
      type: mongoose.Schema.Types.Mixed,
      default: null,
    },
    error_message: {
      type: String,
      default: null,
    },
  },
  {
    timestamps: true,
    collection: 'tx_logs',
  }
);

txLogSchema.index({ idempotency_key: 1, status: 1, createdAt: -1 });

module.exports = mongoose.model('TxLog', txLogSchema);
