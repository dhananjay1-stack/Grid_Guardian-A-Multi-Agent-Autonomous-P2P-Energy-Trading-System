/**
 * relayer/submitChallenge.js — Submit a challenge against a published match
 *
 * Requires the challenger to have approved challengeBond USDC
 * to the MatchRegistry contract.
 *
 * Usage:
 *   node relayer/submitChallenge.js \
 *     --matchHash 0x... \
 *     --altMatchHash 0x...
 */
require("dotenv").config({ path: require("path").resolve(__dirname, "../.env") });
const { ethers } = require("ethers");
const fs   = require("fs");
const path = require("path");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL             = process.env.RPC_URL || "http://127.0.0.1:8545";
const MATCH_REGISTRY_ADDR = process.env.MATCH_REGISTRY_ADDRESS;

// ── Load ABI ───────────────────────────────────────────────────────
function loadRegistryABI() {
  const p = path.resolve(__dirname, "../artifacts/contracts/MatchRegistry.sol/MatchRegistry.json");
  if (!fs.existsSync(p)) {
    throw new Error("MatchRegistry artifact not found. Run `npx hardhat compile` first.");
  }
  return JSON.parse(fs.readFileSync(p, "utf-8")).abi;
}

/**
 * Submit a challenge to MatchRegistry.
 * The caller must have approved USDC for challengeBond amount.
 *
 * @param {ethers.Signer} signer       - Challenger's signer
 * @param {string} contractAddr        - MatchRegistry address
 * @param {string} matchHash           - The match being challenged
 * @param {string} altMatchHash        - Proposed alternative match hash
 * @returns {Promise<ethers.TransactionReceipt>}
 */
async function submitChallenge(signer, contractAddr, matchHash, altMatchHash) {
  const abi = loadRegistryABI();
  const contract = new ethers.Contract(contractAddr, abi, signer);
  const tx = await contract.challengeMatch(matchHash, altMatchHash);
  return tx.wait();
}

module.exports = { submitChallenge };
