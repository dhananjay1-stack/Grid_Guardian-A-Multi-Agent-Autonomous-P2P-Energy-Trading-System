/**
 * adjudicator/mock_adjudicator.js — Mock adjudicator for testing
 *
 * Listens for MatchChallenged events from MatchRegistry and
 * automatically resolves challenges by checking if the alternative
 * match hash differs from the original (simulates basic verification).
 *
 * In production, this would:
 *   1. Fetch the full matchBlob and inputsHash.
 *   2. Reproduce the optimizer run deterministically.
 *   3. Verify constraints are met.
 *   4. Sign an EIP-712 attestation.
 *   5. Call resolveChallenge with a quorum of adjudicator signatures.
 *
 * For testing, it simply resolves challenges as valid or invalid
 * based on a configurable policy.
 *
 * Usage:
 *   node adjudicator/mock_adjudicator.js [--policy accept|reject]
 */
require("dotenv").config({ path: require("path").resolve(__dirname, "../.env") });
const { ethers } = require("ethers");
const fs   = require("fs");
const path = require("path");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL             = process.env.RPC_URL || "http://127.0.0.1:8545";
const MATCH_REGISTRY_ADDR = process.env.MATCH_REGISTRY_ADDRESS;
const ADJUDICATOR_KEY     = process.env.ADJUDICATOR_PRIVATE_KEY || process.env.PRIVATE_KEY;

// Parse CLI args for policy
const policy = process.argv.includes("--policy")
  ? process.argv[process.argv.indexOf("--policy") + 1]
  : "accept"; // default: accept challenges as valid

// ── Load ABI ───────────────────────────────────────────────────────
function loadABI() {
  const p = path.resolve(__dirname, "../artifacts/contracts/MatchRegistry.sol/MatchRegistry.json");
  if (!fs.existsSync(p)) {
    throw new Error("MatchRegistry artifact not found. Run `npx hardhat compile` first.");
  }
  return JSON.parse(fs.readFileSync(p, "utf-8")).abi;
}

async function main() {
  if (!MATCH_REGISTRY_ADDR) {
    console.error("❌  MATCH_REGISTRY_ADDRESS not set in .env");
    process.exit(1);
  }

  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const wallet   = new ethers.Wallet(ADJUDICATOR_KEY, provider);
  const abi      = loadABI();
  const registry = new ethers.Contract(MATCH_REGISTRY_ADDR, abi, wallet);

  console.log(`[Adjudicator] Listening for MatchChallenged events...`);
  console.log(`  Policy: ${policy} (${policy === "accept" ? "challenges are valid" : "challenges are rejected"})`);
  console.log(`  Address: ${wallet.address}`);

  registry.on("MatchChallenged", async (matchHash, altMatchHash, challenger, bondAmount) => {
    console.log(`\n[Adjudicator] MatchChallenged detected:`);
    console.log(`  matchHash    : ${matchHash}`);
    console.log(`  altMatchHash : ${altMatchHash}`);
    console.log(`  challenger   : ${challenger}`);
    console.log(`  bondAmount   : ${bondAmount}`);

    try {
      // In production: run full verification here
      const challengeValid = policy === "accept";

      // Get the optimizer ID (for demo: use a default)
      const optimizerId = ethers.keccak256(ethers.toUtf8Bytes("optimizer-1"));
      const slashAmount = challengeValid ? bondAmount * 2n : 0n;

      console.log(`  Decision: ${challengeValid ? "VALID (slash optimizer)" : "INVALID (reject challenge)"}`);

      const tx = await registry.resolveChallenge(
        matchHash,
        challengeValid,
        optimizerId,
        slashAmount
      );
      const receipt = await tx.wait();
      console.log(`  Resolved — tx: ${receipt.hash}`);
    } catch (err) {
      console.error(`  Resolution failed: ${err.message}`);
    }
  });
}

module.exports = { policy };

if (require.main === module) {
  main().catch((err) => {
    console.error("[Adjudicator] Fatal error:", err);
    process.exit(1);
  });
}
