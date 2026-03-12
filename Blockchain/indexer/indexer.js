/**
 * indexer/indexer.js — Grid-Guardian Commit Indexer
 *
 * Listens to CommitPosted events from TradingSC and builds canonical
 * input lists per roundId. Provides:
 *   - GET /round/:roundId/inputs — sorted commit hashes + inputsHash
 *   - GET /health — liveness probe
 *
 * The inputsHash is computed as:
 *   keccak256(abi.encodePacked(sorted_commit_hashes...))
 *
 * This ensures reproducibility: any optimizer or verifier can fetch
 * the same canonical input set and produce the same inputsHash.
 *
 * Usage:
 *   node indexer/indexer.js
 *
 * Prerequisites:
 *   - TradingSC deployed (TRADING_SC_ADDRESS in .env)
 *   - Hardhat node running
 */
require("dotenv").config({ path: require("path").resolve(__dirname, "../.env") });
const express    = require("express");
const { ethers } = require("ethers");

const app = express();
app.use(express.json());

// ── Config ─────────────────────────────────────────────────────────
const PORT             = process.env.INDEXER_PORT || 4001;
const RPC_URL          = process.env.RPC_URL || "http://127.0.0.1:8545";
const TRADING_SC_ADDR  = process.env.TRADING_SC_ADDRESS;

// ── TradingSC minimal ABI (only events we need) ───────────────────
const TRADING_ABI = [
  "event CommitPosted(bytes32 indexed commitHash, address indexed owner, uint32 roundId, uint32 expiryBlock)",
];

// ── In-memory store: roundId => Set<commitHash> ───────────────────
const rounds = new Map();

/**
 * Compute the canonical inputsHash for a list of commit hashes.
 * Sorts the hashes lexicographically and packs them.
 */
function computeInputsHash(commitHashes) {
  const sorted = [...commitHashes].sort();
  const packed = ethers.solidityPacked(
    sorted.map(() => "bytes32"),
    sorted
  );
  return ethers.keccak256(packed);
}

// ── Start indexer ──────────────────────────────────────────────────
async function startIndexer() {
  if (!TRADING_SC_ADDR) {
    console.warn("⚠  TRADING_SC_ADDRESS not set. Indexer will run in stub mode.");
  }

  if (TRADING_SC_ADDR) {
    const provider = new ethers.JsonRpcProvider(RPC_URL);
    const trading  = new ethers.Contract(TRADING_SC_ADDR, TRADING_ABI, provider);

    // Listen for CommitPosted events
    trading.on("CommitPosted", (commitHash, owner, roundId, expiryBlock) => {
      const rid = Number(roundId);
      if (!rounds.has(rid)) {
        rounds.set(rid, new Set());
      }
      rounds.get(rid).add(commitHash);
      console.log(`[Indexer] CommitPosted: round=${rid}, commit=${commitHash.slice(0, 18)}...`);
    });

    // Also index historical events
    try {
      const filter = trading.filters.CommitPosted();
      const events = await trading.queryFilter(filter, 0, "latest");
      for (const ev of events) {
        const rid = Number(ev.args.roundId);
        if (!rounds.has(rid)) rounds.set(rid, new Set());
        rounds.get(rid).add(ev.args.commitHash);
      }
      console.log(`[Indexer] Indexed ${events.length} historical CommitPosted events`);
    } catch (err) {
      console.warn(`[Indexer] Could not index historical events: ${err.message}`);
    }
  }

  // ── API Endpoints ──────────────────────────────────────────────
  app.get("/health", (_req, res) => {
    res.json({ status: "ok", service: "indexer", roundsTracked: rounds.size });
  });

  app.get("/round/:roundId/inputs", (req, res) => {
    const rid = parseInt(req.params.roundId, 10);
    const commitSet = rounds.get(rid);

    if (!commitSet || commitSet.size === 0) {
      return res.status(404).json({ error: "No commits found for this round" });
    }

    const commitHashes = [...commitSet].sort();
    const inputsHash   = computeInputsHash(commitHashes);

    res.json({
      roundId:      rid,
      commitCount:  commitHashes.length,
      commitHashes,
      inputsHash,
    });
  });

  app.listen(PORT, () => {
    console.log(`[Indexer] Listening on port ${PORT}`);
  });
}

// Export for programmatic use
module.exports = { computeInputsHash, startIndexer };

// Run if executed directly
if (require.main === module) {
  startIndexer().catch((err) => {
    console.error("[Indexer] Fatal error:", err);
    process.exit(1);
  });
}
