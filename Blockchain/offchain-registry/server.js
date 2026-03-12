/**
 * offchain-registry/server.js — Grid-Guardian Off-Chain KYC Registry
 *
 * Stores sensitive PII/KYC data in Postgres. Only a metaURI (IPFS CID or
 * hash pointer) is stored on-chain. Authenticated admins can query PII.
 *
 * Endpoints:
 *   POST /kyc           — store a KYC profile, returns metaURI
 *   GET  /kyc/:nodeId   — retrieve PII (admin auth required)
 *   GET  /health        — liveness probe
 *
 * Security (production checklist):
 *   - Add JWT / API-key middleware for /kyc/:nodeId
 *   - Enable HTTPS / mTLS
 *   - Encrypt raw_profile column at rest (pgcrypto)
 */
require("dotenv").config({ path: require("path").resolve(__dirname, "../.env") });
const express = require("express");
const { Pool } = require("pg");
const { ethers } = require("ethers");
const { v4: uuidv4 } = require("uuid");

const app  = express();
app.use(express.json());

// ── Config ─────────────────────────────────────────────────────────
const PORT         = process.env.REGISTRY_PORT || 5000;
const POSTGRES_URL = process.env.POSTGRES_URL || "postgres://postgres:password@127.0.0.1:5432/gridguardian";

// ── Postgres pool ──────────────────────────────────────────────────
const pool = new Pool({ connectionString: POSTGRES_URL });

// Ensure table exists on startup
(async () => {
  try {
    await pool.query(`
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
    `);
    console.log("✅  Postgres connected — kyc_records table ready.");
  } catch (err) {
    console.error("⚠  Postgres init failed (will retry on request):", err.message);
  }
})();

// ════════════════════════════════════════════════════════════════════
//                          ENDPOINTS
// ════════════════════════════════════════════════════════════════════

app.get("/health", (_req, res) => res.json({ status: "ok", service: "offchain-registry" }));

/**
 * POST /kyc — Store a KYC profile.
 * Body: { household_id, archetype, device_serial, firmware_hash, ... }
 * Returns: { metaURI, nodeId (if derivable) }
 */
app.post("/kyc", async (req, res) => {
  try {
    const profile = req.body;
    const {
      household_id,
      archetype     = "prosumer",
      device_serial = null,
      firmware_hash = null,
    } = profile;

    if (!household_id) {
      return res.status(400).json({ error: "household_id is required" });
    }

    // ── Generate a metaURI from the profile hash ──
    // In production: encrypt profile → upload to IPFS → return CID.
    // Here we create a deterministic hash-based URI.
    const profileHash = ethers.keccak256(
      ethers.toUtf8Bytes(JSON.stringify(profile))
    );
    const metaURI = `ipfs://${profileHash.slice(2)}`;

    // ── Use household_id as the node_id key (or generate one) ──
    const nodeId = profile.nodeId || profileHash;

    // ── Upsert into Postgres ──
    await pool.query(
      `INSERT INTO kyc_records (node_id, household_id, archetype, device_serial, firmware_hash, meta_uri, raw_profile)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       ON CONFLICT (node_id) DO UPDATE SET
         household_id  = EXCLUDED.household_id,
         archetype     = EXCLUDED.archetype,
         device_serial = EXCLUDED.device_serial,
         firmware_hash = EXCLUDED.firmware_hash,
         meta_uri      = EXCLUDED.meta_uri,
         raw_profile   = EXCLUDED.raw_profile,
         updated_at    = NOW()`,
      [nodeId, household_id, archetype, device_serial, firmware_hash, metaURI, profile]
    );

    console.log(`  KYC stored for ${household_id} → metaURI: ${metaURI}`);
    res.json({ metaURI, nodeId, stored: true });
  } catch (err) {
    console.error("KYC store error:", err.message);
    res.status(500).json({ error: err.message });
  }
});

/**
 * GET /kyc/:nodeId — Retrieve PII (admin-only in production).
 *
 * TODO: Add authentication middleware (JWT / API-key).
 */
app.get("/kyc/:nodeId", async (req, res) => {
  try {
    const { nodeId } = req.params;
    const result = await pool.query(
      "SELECT * FROM kyc_records WHERE node_id = $1",
      [nodeId]
    );
    if (result.rows.length === 0) {
      return res.status(404).json({ error: "Node not found" });
    }
    res.json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Start ──
app.listen(PORT, () => {
  console.log(`\n📋  Off-Chain Registry listening on port ${PORT}`);
  console.log(`   Postgres: ${POSTGRES_URL}\n`);
});
