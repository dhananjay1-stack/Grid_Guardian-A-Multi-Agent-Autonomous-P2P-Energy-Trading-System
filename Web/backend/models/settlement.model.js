/**
 * Settlement Record Model (PostgreSQL)
 * Grid-Guardian - Trade Settlement Tracking
 */

const { pool } = require('../config/db.postgres');
const logger = require('../utils/logger');

const SettlementRecord = {
  /**
   * Create settlement record
   */
  async create(data) {
    const {
      trade_id,
      local_trade_id,
      match_hash,
      buyer_node_id,
      seller_node_id,
      kwh_bucket,
      price_bucket,
      locked_amount,
      status = 'INITIATED',
    } = data;

    const query = `
      INSERT INTO settlement_records
      (trade_id, local_trade_id, match_hash, buyer_node_id, seller_node_id,
       kwh_bucket, price_bucket, locked_amount, status)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
      RETURNING *;
    `;

    try {
      const result = await pool.query(query, [
        trade_id,
        local_trade_id,
        match_hash,
        buyer_node_id,
        seller_node_id,
        kwh_bucket,
        price_bucket,
        locked_amount,
        status,
      ]);
      return result.rows[0];
    } catch (error) {
      logger.error('Error creating settlement record:', error);
      throw error;
    }
  },

  /**
   * Find by trade ID
   */
  async findByTradeId(tradeId) {
    const query = 'SELECT * FROM settlement_records WHERE trade_id = $1';
    try {
      const result = await pool.query(query, [tradeId]);
      return result.rows[0] || null;
    } catch (error) {
      logger.error('Error finding settlement record:', error);
      throw error;
    }
  },

  /**
   * Find by node (buyer or seller)
   */
  async findByNode(nodeId, limit = 50) {
    const query = `
      SELECT * FROM settlement_records
      WHERE buyer_node_id = $1 OR seller_node_id = $1
      ORDER BY created_at DESC
      LIMIT $2
    `;
    try {
      const result = await pool.query(query, [nodeId, limit]);
      return result.rows;
    } catch (error) {
      logger.error('Error finding settlements by node:', error);
      throw error;
    }
  },

  /**
   * Find pending settlements
   */
  async findPending() {
    const query = `
      SELECT * FROM settlement_records
      WHERE status IN ('INITIATED', 'LOCKED', 'EXECUTED', 'DELIVERED', 'DELIVERY_CONFIRMED')
      ORDER BY created_at ASC
    `;
    try {
      const result = await pool.query(query);
      return result.rows;
    } catch (error) {
      logger.error('Error finding pending settlements:', error);
      throw error;
    }
  },

  /**
   * Update settlement status
   */
  async updateStatus(tradeId, status, txHash = null, errorMessage = null) {
    const query = `
      UPDATE settlement_records
      SET status = $2,
          tx_hash = COALESCE($3, tx_hash),
          error_message = $4,
          updated_at = CURRENT_TIMESTAMP
      WHERE trade_id = $1
      RETURNING *;
    `;
    try {
      const result = await pool.query(query, [tradeId, status, txHash, errorMessage]);
      return result.rows[0] || null;
    } catch (error) {
      logger.error('Error updating settlement status:', error);
      throw error;
    }
  },

  /**
   * Add receipt to settlement
   */
  async addReceipt(tradeId, nodeId, receiptData) {
    const query = `
      UPDATE settlement_records
      SET receipts = COALESCE(receipts, '[]'::jsonb) || $3::jsonb,
          updated_at = CURRENT_TIMESTAMP
      WHERE trade_id = $1
      RETURNING *;
    `;

    const receipt = JSON.stringify([{
      node_id: nodeId,
      ...receiptData,
      timestamp: Date.now(),
    }]);

    try {
      const result = await pool.query(query, [tradeId, nodeId, receipt]);
      return result.rows[0] || null;
    } catch (error) {
      logger.error('Error adding receipt to settlement:', error);
      throw error;
    }
  },

  /**
   * Get settlement stats
   */
  async getStats() {
    const query = `
      SELECT
        COUNT(*) as total,
        COUNT(CASE WHEN status = 'SETTLED' THEN 1 END) as settled,
        COUNT(CASE WHEN status IN ('INITIATED', 'LOCKED', 'EXECUTED') THEN 1 END) as pending,
        COUNT(CASE WHEN status = 'FAILED' THEN 1 END) as failed,
        SUM(CASE WHEN status = 'SETTLED' THEN CAST(locked_amount AS DECIMAL) ELSE 0 END) as total_settled_amount
      FROM settlement_records
    `;
    try {
      const result = await pool.query(query);
      return result.rows[0];
    } catch (error) {
      logger.error('Error getting settlement stats:', error);
      throw error;
    }
  },

  /**
   * Get recent settlements
   */
  async getRecent(limit = 50) {
    const query = 'SELECT * FROM settlement_records ORDER BY created_at DESC LIMIT $1';
    try {
      const result = await pool.query(query, [limit]);
      return result.rows;
    } catch (error) {
      logger.error('Error getting recent settlements:', error);
      throw error;
    }
  },
};

module.exports = SettlementRecord;
