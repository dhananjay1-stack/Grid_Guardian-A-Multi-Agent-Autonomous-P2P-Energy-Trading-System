/**
 * pi-client/publish_commit_onchain.js — Post a commit on-chain via TradingSC
 *
 * Computes commitHash = keccak256(offerHash | salt) and calls
 * TradingSC.postCommit() either directly or via relayer.
 *
 * Usage:
 *   const { publishCommit } = require('./publish_commit_onchain');
 *   const receipt = await publishCommit(signer, contractAddress, {
 *     commitHash, offerStructHash, roundId, nodeId, expiryBlock, signature
 *   });
 */
const { ethers } = require("ethers");
const path = require("path");
const fs   = require("fs");

// ── Load TradingSC ABI from artifacts ──
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
 * Compute the commit hash from offerHash and salt.
 * @param {string} offerHash - bytes32 offer struct hash
 * @param {string} salt      - bytes32 random salt
 * @returns {string} commitHash
 */
function computeCommitHash(offerHash, salt) {
  return ethers.keccak256(
    ethers.solidityPacked(["bytes32", "bytes32"], [offerHash, salt])
  );
}

/**
 * Post a commit on-chain.
 * @param {ethers.Signer} signer  - The signer paying gas (relayer or direct)
 * @param {string} contractAddr   - TradingSC deployed address
 * @param {object} params         - { commitHash, offerStructHash, roundId, nodeId, expiryBlock, signature }
 * @returns {Promise<ethers.TransactionReceipt>}
 */
async function publishCommit(signer, contractAddr, params) {
  const abi = loadTradingABI();
  const contract = new ethers.Contract(contractAddr, abi, signer);
  const tx = await contract.postCommit(
    params.commitHash,
    params.offerStructHash,
    params.roundId,
    params.nodeId,
    params.expiryBlock,
    params.signature
  );
  return tx.wait();
}

module.exports = { publishCommit, computeCommitHash };
