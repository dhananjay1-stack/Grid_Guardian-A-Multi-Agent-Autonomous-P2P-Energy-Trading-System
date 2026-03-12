/**
 * pi-client/reveal_offer.js — Reveal an offer on-chain via TradingSC
 *
 * Provides the offerHash and salt to TradingSC.revealOffer(), which
 * verifies keccak256(offerHash | salt) == commitHash and marks it revealed.
 *
 * Salt storage:
 *   Salts are stored at ~/.gridguardian/salts/<commitHash>.salt with
 *   POSIX mode 0o600 (owner read/write only). Never upload salts to
 *   untrusted servers.
 *
 * Usage:
 *   const { revealOnChain, loadSalt, storeSalt } = require('./reveal_offer');
 *   await revealOnChain(signer, contractAddr, commitHash, offerHash, salt);
 */
const { ethers } = require("ethers");
const path = require("path");
const fs   = require("fs");
const os   = require("os");

// ── Salt storage paths ─────────────────────────────────────────────
const SALT_DIR = path.join(os.homedir(), ".gridguardian", "salts");

/**
 * Store a salt securely on the local filesystem.
 * @param {string} commitHash - The commit hash (used as filename)
 * @param {string} salt       - The bytes32 salt hex string
 */
function storeSalt(commitHash, salt) {
  if (!fs.existsSync(SALT_DIR)) {
    fs.mkdirSync(SALT_DIR, { recursive: true });
  }
  const filepath = path.join(SALT_DIR, `${commitHash}.salt`);
  fs.writeFileSync(filepath, salt, { mode: 0o600 });
  console.log(`  Salt stored: ${filepath}`);
}

/**
 * Load a previously stored salt.
 * @param {string} commitHash - The commit hash
 * @returns {string} The salt hex string
 */
function loadSalt(commitHash) {
  const filepath = path.join(SALT_DIR, `${commitHash}.salt`);
  if (!fs.existsSync(filepath)) {
    throw new Error(`Salt not found for commit ${commitHash}`);
  }
  return fs.readFileSync(filepath, "utf-8").trim();
}

/**
 * Load TradingSC ABI from artifacts.
 */
function loadTradingABI() {
  const artifactPath = path.resolve(
    __dirname, "../artifacts/contracts/TradingSC.sol/TradingSC.json"
  );
  if (!fs.existsSync(artifactPath)) {
    throw new Error("TradingSC artifact not found. Run `npx hardhat compile` first.");
  }
  return JSON.parse(fs.readFileSync(artifactPath, "utf-8")).abi;
}

/**
 * Reveal an offer on-chain.
 * @param {ethers.Signer} signer       - The signer paying gas
 * @param {string}        contractAddr - TradingSC address
 * @param {string}        commitHash   - The commitment to reveal
 * @param {string}        offerHash    - The offer struct hash
 * @param {string}        salt         - The bytes32 salt
 * @returns {Promise<ethers.TransactionReceipt>}
 */
async function revealOnChain(signer, contractAddr, commitHash, offerHash, salt) {
  const abi = loadTradingABI();
  const contract = new ethers.Contract(contractAddr, abi, signer);
  const tx = await contract.revealOffer(commitHash, offerHash, salt);
  return tx.wait();
}

module.exports = { revealOnChain, storeSalt, loadSalt };
