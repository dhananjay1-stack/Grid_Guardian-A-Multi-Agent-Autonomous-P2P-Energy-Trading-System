const { pool } = require('../config/db.postgres');
const logger = require('../utils/logger');

const Trade = {
  // Create a new trade
  async create(tradeData) {
    const {
      trade_id,
      node_id,
      trade_type,
      energy_amount,
      price_per_unit,
      total_price,
      status = 'PENDING',
      counterparty_id = null,
      blockchain_tx_hash = null,
    } = tradeData;

    const query = `
      INSERT INTO trades
      (trade_id, node_id, trade_type, energy_amount, price_per_unit, total_price, status, counterparty_id, blockchain_tx_hash)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
      RETURNING *;
    `;

    try {
      const result = await pool.query(query, [
        trade_id,
        node_id,
        trade_type,
        energy_amount,
        price_per_unit,
        total_price,
        status,
        counterparty_id,
        blockchain_tx_hash,
      ]);
      return result.rows[0];
    } catch (error) {
      logger.error('Error creating trade:', error);
      throw error;
    }
  },

  // Find trade by ID
  async findById(id) {
    const query = 'SELECT * FROM trades WHERE id = $1';
    try {
      const result = await pool.query(query, [id]);
      return result.rows[0] || null;
    } catch (error) {
      logger.error('Error finding trade by ID:', error);
      throw error;
    }
  },

  // Find trade by trade_id
  async findByTradeId(tradeId) {
    const query = 'SELECT * FROM trades WHERE trade_id = $1';
    try {
      const result = await pool.query(query, [tradeId]);
      return result.rows[0] || null;
    } catch (error) {
      logger.error('Error finding trade by trade_id:', error);
      throw error;
    }
  },

  // Get trades by node
  async findByNodeId(nodeId, limit = 50, offset = 0) {
    const query = `
      SELECT * FROM trades
      WHERE node_id = $1
      ORDER BY created_at DESC
      LIMIT $2 OFFSET $3
    `;
    try {
      const result = await pool.query(query, [nodeId, limit, offset]);
      return result.rows;
    } catch (error) {
      logger.error('Error finding trades by node:', error);
      throw error;
    }
  },

  // Update trade status
  async updateStatus(tradeId, status, txHash = null) {
    const query = `
      UPDATE trades
      SET status = $2, blockchain_tx_hash = COALESCE($3, blockchain_tx_hash), updated_at = CURRENT_TIMESTAMP
      WHERE trade_id = $1
      RETURNING *;
    `;
    try {
      const result = await pool.query(query, [tradeId, status, txHash]);
      return result.rows[0] || null;
    } catch (error) {
      logger.error('Error updating trade status:', error);
      throw error;
    }
  },

  // Get recent trades
  async getRecent(limit = 100) {
    const query = 'SELECT * FROM trades ORDER BY created_at DESC LIMIT $1';
    try {
      const result = await pool.query(query, [limit]);
      return result.rows;
    } catch (error) {
      logger.error('Error getting recent trades:', error);
      throw error;
    }
  },

  // Get all trades with limit
  async findAll(limit = 50) {
    const query = 'SELECT * FROM trades ORDER BY created_at DESC LIMIT $1';
    try {
      const result = await pool.query(query, [limit]);
      return result.rows;
    } catch (error) {
      logger.error('Error finding all trades:', error);
      throw error;
    }
  },

  // Get active trades (PENDING, MATCHED, EXECUTED)
  async findActive() {
    const query = `
      SELECT * FROM trades
      WHERE status IN ('PENDING', 'MATCHED', 'EXECUTED')
      ORDER BY created_at DESC
    `;
    try {
      const result = await pool.query(query);
      return result.rows;
    } catch (error) {
      logger.error('Error finding active trades:', error);
      throw error;
    }
  },
};

module.exports = Trade;
