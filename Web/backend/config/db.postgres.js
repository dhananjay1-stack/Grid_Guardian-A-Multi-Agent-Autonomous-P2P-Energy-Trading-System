const { Pool } = require('pg');
const logger = require('../utils/logger');

const pool = new Pool({
  host: process.env.PG_HOST || 'localhost',
  port: parseInt(process.env.PG_PORT) || 5432,
  database: process.env.PG_DATABASE || 'gridguardian',
  user: process.env.PG_USER || 'postgres',
  password: process.env.PG_PASSWORD || 'postgres',
  max: 20,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 2000,
});

pool.on('error', (err) => {
  logger.error('PostgreSQL pool error:', err);
});

// Track connection status
let isConnected = false;

const connectPostgres = async () => {
  // Skip PostgreSQL if explicitly disabled
  if (process.env.PG_ENABLED === 'false') {
    logger.warn('PostgreSQL disabled via PG_ENABLED=false');
    return null;
  }

  try {
    const client = await pool.connect();
    logger.info('PostgreSQL connected successfully');

    // Create trades table if not exists
    await client.query(`
      CREATE TABLE IF NOT EXISTS trades (
        id SERIAL PRIMARY KEY,
        trade_id VARCHAR(100) UNIQUE NOT NULL,
        node_id VARCHAR(50) NOT NULL,
        trade_type VARCHAR(20) NOT NULL,
        energy_amount DECIMAL(10, 4) NOT NULL,
        price_per_unit DECIMAL(10, 4) NOT NULL,
        total_price DECIMAL(12, 4) NOT NULL,
        status VARCHAR(20) DEFAULT 'PENDING',
        counterparty_id VARCHAR(50),
        blockchain_tx_hash VARCHAR(100),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS settlement_records (
        id SERIAL PRIMARY KEY,
        trade_id VARCHAR(130) UNIQUE NOT NULL,
        local_trade_id VARCHAR(100),
        match_hash VARCHAR(130),
        buyer_node_id VARCHAR(130) NOT NULL,
        seller_node_id VARCHAR(130) NOT NULL,
        kwh_bucket INTEGER NOT NULL,
        price_bucket INTEGER NOT NULL,
        locked_amount VARCHAR(80) NOT NULL,
        tx_hash VARCHAR(130),
        status VARCHAR(32) DEFAULT 'INITIATED',
        error_message TEXT,
        receipts JSONB DEFAULT '[]'::jsonb,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS delivery_receipts (
        id SERIAL PRIMARY KEY,
        trade_id VARCHAR(130) NOT NULL,
        node_id VARCHAR(130) NOT NULL,
        meter_snapshot_hash VARCHAR(130),
        meter_reading VARCHAR(130),
        delivered_kwh_bucket INTEGER,
        period_start TIMESTAMP,
        period_end TIMESTAMP,
        nonce BIGINT,
        tx_hash VARCHAR(130),
        block_number BIGINT,
        status VARCHAR(32) DEFAULT 'REQUESTED',
        command_id VARCHAR(64),
        error_message TEXT,
        on_chain_data JSONB,
        submitted_at TIMESTAMP,
        verified_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(trade_id, node_id)
      );
    `);

    await client.query('CREATE INDEX IF NOT EXISTS idx_settlement_status ON settlement_records(status);');
    await client.query('CREATE INDEX IF NOT EXISTS idx_settlement_node_pair ON settlement_records(buyer_node_id, seller_node_id);');
    await client.query('CREATE INDEX IF NOT EXISTS idx_receipts_status ON delivery_receipts(status);');
    await client.query('CREATE INDEX IF NOT EXISTS idx_receipts_trade ON delivery_receipts(trade_id);');

    logger.info('PostgreSQL blockchain tables initialized');
    client.release();
    isConnected = true;

    return pool;
  } catch (error) {
    logger.warn('PostgreSQL connection failed (running in degraded mode):', error.message);
    logger.warn('Trade settlement features will be unavailable');
    isConnected = false;
    // Don't throw - allow backend to run without PostgreSQL
    return null;
  }
};

const isPostgresConnected = () => isConnected;

const disconnectPostgres = async () => {
  try {
    await pool.end();
    logger.info('PostgreSQL connection pool closed');
  } catch (error) {
    logger.error('Error closing PostgreSQL pool:', error.message);
    throw error;
  }
};

module.exports = { pool, connectPostgres, disconnectPostgres, isPostgresConnected };
