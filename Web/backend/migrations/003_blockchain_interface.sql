-- Grid-Guardian Blockchain Interface Database Schema
-- Run this after the initial database setup

-- Settlement Records Table
CREATE TABLE IF NOT EXISTS settlement_records (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(66) UNIQUE NOT NULL,  -- bytes32 hex string
    local_trade_id VARCHAR(32) REFERENCES trades(trade_id),
    match_hash VARCHAR(66),
    buyer_node_id VARCHAR(66) NOT NULL,
    seller_node_id VARCHAR(66) NOT NULL,
    kwh_bucket INTEGER NOT NULL,
    price_bucket INTEGER NOT NULL,
    locked_amount VARCHAR(78) NOT NULL,  -- uint256 as string
    status VARCHAR(32) NOT NULL DEFAULT 'INITIATED',
    tx_hash VARCHAR(66),
    error_message TEXT,
    receipts JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Settlement status index
CREATE INDEX IF NOT EXISTS idx_settlement_status ON settlement_records(status);
CREATE INDEX IF NOT EXISTS idx_settlement_buyer ON settlement_records(buyer_node_id);
CREATE INDEX IF NOT EXISTS idx_settlement_seller ON settlement_records(seller_node_id);
CREATE INDEX IF NOT EXISTS idx_settlement_created ON settlement_records(created_at DESC);

-- Delivery Receipts Table
CREATE TABLE IF NOT EXISTS delivery_receipts (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(66) NOT NULL,
    node_id VARCHAR(66) NOT NULL,
    meter_snapshot_hash VARCHAR(66),
    meter_reading DECIMAL(18, 4),
    delivered_kwh_bucket INTEGER,
    period_start TIMESTAMP WITH TIME ZONE,
    period_end TIMESTAMP WITH TIME ZONE,
    nonce INTEGER,
    tx_hash VARCHAR(66),
    block_number BIGINT,
    status VARCHAR(32) NOT NULL DEFAULT 'REQUESTED',
    command_id VARCHAR(32),
    error_message TEXT,
    on_chain_data JSONB,
    verified_at TIMESTAMP WITH TIME ZONE,
    submitted_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_id, node_id)
);

-- Receipt indexes
CREATE INDEX IF NOT EXISTS idx_receipt_trade ON delivery_receipts(trade_id);
CREATE INDEX IF NOT EXISTS idx_receipt_node ON delivery_receipts(node_id);
CREATE INDEX IF NOT EXISTS idx_receipt_status ON delivery_receipts(status);
CREATE INDEX IF NOT EXISTS idx_receipt_created ON delivery_receipts(created_at DESC);

-- Update counterparty_id column if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'trades' AND column_name = 'counterparty_id'
    ) THEN
        ALTER TABLE trades ADD COLUMN counterparty_id VARCHAR(66);
    END IF;
END $$;

-- Add updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
DROP TRIGGER IF EXISTS update_settlement_records_updated_at ON settlement_records;
CREATE TRIGGER update_settlement_records_updated_at
    BEFORE UPDATE ON settlement_records
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_delivery_receipts_updated_at ON delivery_receipts;
CREATE TRIGGER update_delivery_receipts_updated_at
    BEFORE UPDATE ON delivery_receipts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Add block_number to blockchain_logs enum (MongoDB handled separately)
-- Note: For MongoDB blockchain_logs, add event types via application code

-- Grant permissions (adjust user as needed)
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO grid;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO grid;
