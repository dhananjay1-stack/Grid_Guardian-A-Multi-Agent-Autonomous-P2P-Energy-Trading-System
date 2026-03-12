/**
 * scripts/integration/registerFlow.js
 *
 * End-to-end integration test:
 *   1. Connect to running Hardhat node.
 *   2. Deploy IdentitySC (or use existing).
 *   3. Simulate Pi: generate keys, compute nodeId, create profile.
 *   4. Store KYC in off-chain registry (if available).
 *   5. Sign EIP-712 RegisterNode message.
 *   6. Submit via relayer or directly.
 *   7. Assert on-chain state matches expectations.
 *   8. Run device attestation.
 *   9. Verify final state.
 *
 * Prerequisites:
 *   - Hardhat node running on :8545
 *   - (Optional) Relayer running on :4000
 *   - (Optional) Off-chain registry running on :5000
 *
 * Usage:
 *   node scripts/integration/registerFlow.js
 */
require("dotenv").config();
const { ethers } = require("ethers");
const crypto = require("crypto");
const path   = require("path");
const fs     = require("fs");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL          = process.env.RPC_URL || "http://127.0.0.1:8545";
const DEPLOYER_KEY     = process.env.PRIVATE_KEY;
const RELAYER_KEY      = process.env.RELAYER_PRIVATE_KEY;

// ── Contract ABI (load from artifacts if built, else inline) ──────
let IDENTITY_ABI, IDENTITY_BYTECODE;
const artifactPath = path.resolve(__dirname, "../../artifacts/contracts/IdentitySC.sol/IdentitySC.json");

if (fs.existsSync(artifactPath)) {
  const artifact   = JSON.parse(fs.readFileSync(artifactPath, "utf-8"));
  IDENTITY_ABI      = artifact.abi;
  IDENTITY_BYTECODE = artifact.bytecode;
} else {
  console.error("❌  Artifacts not found. Run `npx hardhat compile` first.");
  process.exit(1);
}

// ── EIP-712 types ──────────────────────────────────────────────────
const EIP712_TYPES = {
  RegisterNode: [
    { name: "nodeId",     type: "bytes32" },
    { name: "pubkeyHash", type: "bytes32" },
    { name: "metaURI",    type: "string"  },
    { name: "nonce",      type: "uint256" },
    { name: "expiry",     type: "uint256" },
  ],
};

async function main() {
  console.log("═══════════════════════════════════════════════════════");
  console.log("  Grid-Guardian — Integration Test: Register Flow");
  console.log("═══════════════════════════════════════════════════════\n");

  const provider       = new ethers.JsonRpcProvider(RPC_URL);
  // Use JsonRpcSigner (node manages signing & nonces for Hardhat accounts)
  const deployer       = await provider.getSigner(0);
  const relayerWallet  = await provider.getSigner(1);

  // ── 1. Deploy IdentitySC ──
  console.log("1️⃣  Deploying IdentitySC...");
  const deployerAddr = await deployer.getAddress();
  const factory  = new ethers.ContractFactory(IDENTITY_ABI, IDENTITY_BYTECODE, deployer);
  const identity = await factory.deploy(deployerAddr);
  await identity.waitForDeployment();
  const contractAddr = await identity.getAddress();
  console.log(`   ✅  Deployed at: ${contractAddr}\n`);

  // ── Grant RELAYER_ROLE ──
  const RELAYER_ROLE = ethers.keccak256(ethers.toUtf8Bytes("RELAYER_ROLE"));
  const relayerAddr  = await relayerWallet.getAddress();
  const grantTx1 = await identity.grantRole(RELAYER_ROLE, relayerAddr);
  await grantTx1.wait();
  console.log(`   ✅  RELAYER_ROLE → ${relayerAddr}\n`);

  // ── Grant ADMIN_ROLE to relayer too (so it can call attestNode) ──
  const ADMIN_ROLE = ethers.keccak256(ethers.toUtf8Bytes("ADMIN_ROLE"));
  const grantTx2 = await identity.grantRole(ADMIN_ROLE, relayerAddr);
  await grantTx2.wait();

  // ── 2. Simulate Pi: generate device wallet ──
  console.log("2️⃣  Generating device keys...");
  const deviceWallet = ethers.Wallet.createRandom().connect(provider);
  const salt         = crypto.randomBytes(32);
  const pubkeyBytes  = ethers.getBytes(deviceWallet.signingKey.compressedPublicKey);
  const nodeId       = ethers.keccak256(ethers.concat([pubkeyBytes, salt]));
  const pubkeyHash   = ethers.keccak256(pubkeyBytes);
  console.log(`   Address   : ${deviceWallet.address}`);
  console.log(`   nodeId    : ${nodeId}`);
  console.log(`   pubkeyHash: ${pubkeyHash}\n`);

  // ── 3. Create metaURI ──
  const profile = {
    household_id:  deviceWallet.address,
    archetype:     "prosumer",
    device_serial: crypto.randomBytes(8).toString("hex"),
    firmware_hash: ethers.keccak256(ethers.toUtf8Bytes("fw-v1.0")),
  };
  const metaURI = `ipfs://${ethers.keccak256(ethers.toUtf8Bytes(JSON.stringify(profile))).slice(2)}`;
  console.log(`3️⃣  metaURI: ${metaURI}\n`);

  // ── 4. Sign EIP-712 message ──
  console.log("4️⃣  Signing EIP-712 RegisterNode...");
  const domain = {
    name:              "GridGuardian-Relayer",
    version:           "1",
    chainId:           31337,
    verifyingContract: contractAddr,
  };
  const msgNonce  = 0;
  const expiry = Math.floor(Date.now() / 1000) + 3600;
  const message = { nodeId, pubkeyHash, metaURI, nonce: msgNonce, expiry };

  const signature = await deviceWallet.signTypedData(domain, EIP712_TYPES, message);
  console.log(`   Signature: ${signature.slice(0, 20)}...${signature.slice(-8)}\n`);

  // ── 5. Relayer submits meta-tx ──
  console.log("5️⃣  Relayer submitting registerNodeMeta...");
  const relayerContract = new ethers.Contract(contractAddr, IDENTITY_ABI, relayerWallet);
  const tx = await relayerContract.registerNodeMeta(
    nodeId, pubkeyHash, metaURI, msgNonce, expiry, signature
  );
  const receipt = await tx.wait();
  console.log(`   ✅  tx: ${receipt.hash}\n`);

  // ── 6. Verify on-chain state ──
  console.log("6️⃣  Verifying on-chain state...");
  const node = await identity.nodes(nodeId);
  assert(node.owner === deviceWallet.address, "owner mismatch");
  assert(node.pubkeyHash === pubkeyHash,       "pubkeyHash mismatch");
  assert(node.metaURI === metaURI,              "metaURI mismatch");
  assert(node.active === true,                  "node not active");
  assert(node.attested === false,               "node should not be attested yet");
  console.log("   ✅  All fields match.\n");

  // ── 7. Check event emitted ──
  const filter = identity.filters.NodeRegistered(nodeId);
  const events = await identity.queryFilter(filter);
  assert(events.length === 1, "NodeRegistered event not found");
  console.log("   ✅  NodeRegistered event emitted.\n");

  // ── 8. Simulate attestation ──
  console.log("7️⃣  Attesting node...");
  const attestTx = await relayerContract.attestNode(nodeId);
  await attestTx.wait();
  const attestedNode = await identity.nodes(nodeId);
  assert(attestedNode.attested === true, "attestation failed");
  console.log("   ✅  Node attested.\n");

  // ── 9. Test nonce increment ──
  const currentNonce = await identity.nonces(deviceWallet.address);
  assert(Number(currentNonce) === 1, "nonce not incremented");
  console.log("   ✅  Nonce incremented to 1.\n");

  // ── Done ──
  console.log("═══════════════════════════════════════════════════════");
  console.log("  ✅  ALL INTEGRATION CHECKS PASSED");
  console.log("═══════════════════════════════════════════════════════\n");
}

function assert(condition, message) {
  if (!condition) {
    console.error(`❌  ASSERTION FAILED: ${message}`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("❌  Integration test failed:", err);
  process.exit(1);
});
