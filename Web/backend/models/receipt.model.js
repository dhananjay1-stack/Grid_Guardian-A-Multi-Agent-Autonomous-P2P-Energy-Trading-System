/**
 * Receipt Model (PostgreSQL)
 * Grid-Guardian - Delivery Receipt Tracking
 */

const { pool } = require('../config/db.postgres');
const logger = require('../utils/logger');

const Receipt = {
  /**
   * Create receipt record
   */
  async create(data) {
    const {
      trade_id,
      node_id,
      meter_snapshot_hash = null,
      meter_reading = null,
      delivered_kwh_bucket = null,
      period_start = null,
      period_end = null,
      nonce = null,
      tx_hash = null,
      block_number = null,
      status = 'REQUESTED',
      command_id = null,
      error_message = null,
    } = data;

    const query = `
      INSERT INTO delivery_receipts
      (trade_id, node_id, meter_snapshot_hash, meter_reading, delivered_kwh_bucket,
       period_start, period_end, nonce, tx_hash, block_number, status, command_id, error_message)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
      RETURNING *;
    `;

    try {
      const result = await pool.query(query, [
        trade_id,
        node_id,
        meter_snapshot_hash,
        meter_reading,
        delivered_kwh_bucket,
        period_start,
        period_end,
        nonce,
        tx_hash,
        block_number,
        status,
        command_id,
        error_message,
      ]);
      return result.rows[0];
    } catch (error) {
      logger.error('Error creating receipt:', error);
      throw error;
    }
  },

  /**
   * Upsert receipt (insert or update)
   */
  async upsert(data) {
    const {
      trade_id,
      node_id,
      meter_snapshot_hash,
      meter_reading,
      delivered_kwh_bucket,
      period_start,
      period_end,
      nonce,
      tx_hash,
      block_number,
      status,
      error_message,
    } = data;

    const query = `
      INSERT INTO delivery_receipts
      (trade_id, node_id, meter_snapshot_hash, meter_reading, delivered_kwh_bucket,
       period_start, period_end, nonce, tx_hash, block_number, status, error_message)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
      ON CONFLICT (trade_id, node_id) DO UPDATE SET
        meter_snapshot_hash = COALESCE(EXCLUDED.meter_snapshot_hash, delivery_receipts.meter_snapshot_hash),
        meter_reading = COALESCE(EXCLUDED.meter_reading, delivery_receipts.meter_reading),
        delivered_kwh_bucket = COALESCE(EXCLUDED.delivered_kwh_bucket, delivery_receipts.delivered_kwh_bucket),
        period_start = COALESCE(EXCLUDED.period_start, delivery_receipts.period_start),
        period_end = COALESCE(EXCLUDED.period_end, delivery_receipts.period_end),
        nonce = COALESCE(EXCLUDED.nonce, delivery_receipts.nonce),
        tx_hash = COALESCE(EXCLUDED.tx_hash, delivery_receipts.tx_hash),
        block_number = COALESCE(EXCLUDED.block_number, delivery_receipts.block_number),
        status = EXCLUDED.status,
        error_message = EXCLUDED.error_message,
        updated_at = CURRENT_TIMESTAMP
      RETURNING *;
    `;

    try {
      const result = await pool.query(query, [
        trade_id,
        node_id,
        meter_snapshot_hash,
        meter_reading,
        delivered_kwh_bucket,
        period_start,
        period_end,
        nonce,
        tx_hash,
        block_number,
        status,
        error_message,
      ]);
      return result.rows[0];
    } catch (error) {
      logger.error('Error upserting receipt:', error);
      throw error;
    }
  },

  /**
   * Find by trade and node
   */
  async findByTradeAndNode(tradeId, nodeId) {
    const query = 'SELECT * FROM delivery_receipts WHERE trade_id = $1 AND node_id = $2';
    try {
      const result = await pool.query(query, [tradeId, nodeId]);
      return result.rows[0] || null;
    } catch (error) {
      logger.error('Error finding receipt:', error);
      throw error;
    }
  },

  /**
   * Find all receipts for a trade
   */
  async findByTrade(tradeId) {
    const query = 'SELECT * FROM delivery_receipts WHERE trade_id = $1 ORDER BY created_at DESC';
    try {
      const result = await pool.query(query, [tradeId]);
      return result.rows;
    } catch (error) {
      logger.error('Error finding receipts by trade:', error);
      throw error;
    }
  },

  /**
   * Find receipts by node
   */
  async findByNode(nodeId, limit = 50) {
    const query = `
      SELECT * FROM delivery_receipts
      WHERE node_id = $1
      ORDER BY created_at DESC
      LIMIT $2
    `;
    try {
      const result = await pool.query(query, [nodeId, limit]);
      return result.rows;
    } catch (error) {
      logger.error('Error finding receipts by node:', error);
      throw error;
    }
  },

  /**
   * Mark receipt as verified
   */
  async markVerified(tradeId, nodeId, onChainData) {
    const query = `
      UPDATE delivery_receipts
      SET status = 'VERIFIED',
          on_chain_data = $3,
          verified_at = CURRENT_TIMESTAMP,
          updated_at = CURRENT_TIMESTAMP
      WHERE trade_id = $1 AND node_id = $2
      RETURNING *;
    `;
    try {
      const result = await pool.query(query, [tradeId, nodeId, JSON.stringify(onChainData)]);
      return result.rows[0] || null;
    } catch (error) {
      logger.error('Error marking receipt verified:', error);
      throw error;
    }
  },

  /**
   * Get count of submitted receipts for a trade
   */
  async getSubmittedCount(tradeId) {
    const query = `
      SELECT COUNT(*) as count
      FROM delivery_receipts
      WHERE trade_id = $1 AND status IN ('SUBMITTED', 'VERIFIED')
    `;
    try {
      const result = await pool.query(query, [tradeId]);
      return parseInt(result.rows[0].count, 10);
    } catch (error) {
      logger.error('Error getting receipt count:', error);
      throw error;
    }
  },

  /**
   * Get receipt stats
   */
  async getStats() {
    const query = `
      SELECT
        COUNT(*) as total,
        COUNT(CASE WHEN status = 'SUBMITTED' THEN 1 END) as submitted,
        COUNT(CASE WHEN status = 'VERIFIED' THEN 1 END) as verified,
        COUNT(CASE WHEN status = 'FAILED' THEN 1 END) as failed,
        COUNT(CASE WHEN status = 'REQUESTED' THEN 1 END) as pending
      FROM delivery_receipts
    `;
    try {
      const result = await pool.query(query);
      return result.rows[0];
    } catch (error) {
      logger.error('Error getting receipt stats:', error);
      throw error;
    }
  },
};

module.exports = Receipt;
