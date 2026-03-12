-- offchain-registry/init.sql
-- Auto-runs when Postgres container starts for the first time.

CREATE TABLE IF NOT EXISTS kyc_records (
    id            SERIAL PRIMARY KEY,
    node_id       TEXT UNIQUE,
    household_id  TEXT NOT NULL,
    archetype     TEXT DEFAULT 'prosumer',
    device_serial TEXT,
    firmware_hash TEXT,
    meta_uri      TEXT,
    raw_profile   JSONB,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kyc_node_id ON kyc_records(node_id);
CREATE INDEX IF NOT EXISTS idx_kyc_household ON kyc_records(household_id);
