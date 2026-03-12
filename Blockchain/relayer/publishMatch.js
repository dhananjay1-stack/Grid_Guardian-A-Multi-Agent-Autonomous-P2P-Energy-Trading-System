/**
 * relayer/publishMatch.js — Sign and publish a match result on-chain
 *
 * Collects m-of-n EIP-712 signatures from operator keys,
 * then calls MatchRegistry.publishMatch().
 *
 * Usage:
 *   node relayer/publishMatch.js \
 *     --matchBlob '{"trades":[...]}' \
 *     --roundId 1 \
 *     --inputsHash 0x... \
 *     --optimizerId 0x...
 */
require("dotenv").config({ path: require("path").resolve(__dirname, "../.env") });
const { ethers } = require("ethers");
const fs   = require("fs");
const path = require("path");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL               = process.env.RPC_URL || "http://127.0.0.1:8545";
const MATCH_REGISTRY_ADDR   = process.env.MATCH_REGISTRY_ADDRESS;
const CHAIN_ID              = parseInt(process.env.CHAIN_ID || "31337", 10);

// ── Load ABI ───────────────────────────────────────────────────────
function loadABI() {
  const p = path.resolve(__dirname, "../artifacts/contracts/MatchRegistry.sol/MatchRegistry.json");
  if (!fs.existsSync(p)) {
    throw new Error("MatchRegistry artifact not found. Run `npx hardhat compile` first.");
  }
  return JSON.parse(fs.readFileSync(p, "utf-8")).abi;
}

// ── EIP-712 types ──────────────────────────────────────────────────
const MATCHRESULT_TYPES = {
  MatchResult: [
    { name: "roundId",      type: "uint32"  },
    { name: "matchHash",    type: "bytes32" },
    { name: "inputsHash",   type: "bytes32" },
    { name: "publishNonce", type: "uint256" },
    { name: "timestamp",    type: "uint256" },
  ],
};

function getMatchDomain(verifyingContract, chainId = 31337) {
  return {
    name:              "GridGuardian-Match",
    version:           "1",
    chainId,
    verifyingContract,
  };
}

/**
 * Collect EIP-712 signatures from operator wallets.
 * @param {ethers.Wallet[]} operatorWallets - Array of operator wallets
 * @param {string} contractAddr             - MatchRegistry address
 * @param {object} message                  - { roundId, matchHash, inputsHash, publishNonce, timestamp }
 * @returns {Promise<string>} Concatenated signatures
 */
async function collectSignatures(operatorWallets, contractAddr, message) {
  const domain = getMatchDomain(contractAddr, CHAIN_ID);
  const sigs = [];

  for (const wallet of operatorWallets) {
    const sig = await wallet.signTypedData(domain, MATCHRESULT_TYPES, message);
    sigs.push(sig);
    console.log(`  Operator ${wallet.address} signed ✔`);
  }

  // Concatenate signatures (remove 0x prefix for all but first)
  return "0x" + sigs.map(s => s.slice(2)).join("");
}

/**
 * Publish a match on-chain.
 * @param {ethers.Signer} signer        - Relayer/publisher signer
 * @param {string} contractAddr         - MatchRegistry address
 * @param {object} params               - { matchHash, inputsHash, roundId, optimizerId, sigTimestamp, signatures }
 */
async function publishMatch(signer, contractAddr, params) {
  const abi = loadABI();
  const contract = new ethers.Contract(contractAddr, abi, signer);
  const tx = await contract.publishMatch(
    params.matchHash,
    params.inputsHash,
    params.roundId,
    params.optimizerId,
    params.sigTimestamp,
    params.signatures
  );
  return tx.wait();
}

module.exports = {
  collectSignatures,
  publishMatch,
  getMatchDomain,
  MATCHRESULT_TYPES,
};
