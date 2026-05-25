/**
 * pi-client/submit_delivery.js
 *
 * Submit a signed delivery receipt to the blockchain via relayer.
 * This allows Pi nodes to report delivery without holding gas tokens.
 *
 * Usage:
 *   node submit_delivery.js <receipt_file_path>
 *   node submit_delivery.js receipts/receipt_0x1234...json
 *
 * Environment:
 *   - RELAYER_URL: Relayer endpoint (default http://127.0.0.1:4000)
 *   - RPC_URL: Direct RPC for fallback submission
 *   - DELIVERY_REGISTRY_ADDRESS: Deployed DeliveryRegistry address
 */
require("dotenv").config();
const { ethers } = require("ethers");
const axios = require("axios");
const fs = require("fs");
const path = require("path");

// ── Config ─────────────────────────────────────────────────────────
const RELAYER_URL = process.env.RELAYER_URL || "http://127.0.0.1:4000";
const RPC_URL = process.env.RPC_URL || "http://127.0.0.1:8545";
const DELIVERY_REGISTRY_ADDR = process.env.DELIVERY_REGISTRY_ADDRESS;

// DeliveryRegistry ABI (for direct submission fallback)
const DELIVERY_ABI = [
  "function submitReceipt(bytes32 tradeId, bytes32 nodeId, bytes32 meterSnapshotHash, uint16 deliveredKwhBucket, uint256 periodStart, uint256 periodEnd, uint256 nonce, bytes signature) external",
  "function getReceiptCount(bytes32 tradeId) view returns (uint256)",
  "event DeliveryReceiptSubmitted(bytes32 indexed tradeId, bytes32 indexed nodeId, bytes32 meterSnapshotHash, uint16 deliveredKwhBucket, uint256 submittedBlock)",
];

async function main() {
  const args = process.argv.slice(2);
  if (args.length < 1) {
    console.log("Usage: node submit_delivery.js <receipt_file_path>");
    console.log("  receipt_file_path: Path to the signed receipt JSON file");
    process.exit(1);
  }

  const receiptPath = args[0];

  if (!fs.existsSync(receiptPath)) {
    console.error(`Receipt file not found: ${receiptPath}`);
    process.exit(1);
  }

  // ── 1. Load receipt ──
  console.log(`Loading receipt from: ${receiptPath}`);
  const receipt = JSON.parse(fs.readFileSync(receiptPath, "utf-8"));

  console.log("\nReceipt details:");
  console.log(`  Trade ID     : ${receipt.tradeId}`);
  console.log(`  Node ID      : ${receipt.nodeId}`);
  console.log(`  Meter Hash   : ${receipt.meterSnapshotHash}`);
  console.log(`  kWh Bucket   : ${receipt.deliveredKwhBucket}`);
  console.log(`  Period       : ${new Date(receipt.periodStart * 1000).toISOString()} - ${new Date(receipt.periodEnd * 1000).toISOString()}`);
  console.log(`  Nonce        : ${receipt.nonce}`);
  console.log(`  Signed by    : ${receipt.signer}`);
  console.log(`  Signed at    : ${receipt.signedAt}`);

  // ── 2. Try submitting via relayer first ──
  console.log("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log("Attempting submission via relayer...");

  try {
    const relayerPayload = {
      tradeId: receipt.tradeId,
      nodeId: receipt.nodeId,
      meterSnapshotHash: receipt.meterSnapshotHash,
      deliveredKwhBucket: receipt.deliveredKwhBucket,
      periodStart: receipt.periodStart,
      periodEnd: receipt.periodEnd,
      nonce: receipt.nonce,
      signature: receipt.signature,
    };

    const resp = await axios.post(`${RELAYER_URL}/api/delivery`, relayerPayload, {
      timeout: 30000,
    });

    console.log("Relayer submission successful!");
    console.log("  Response:", JSON.stringify(resp.data, null, 2));
    return;
  } catch (err) {
    if (err.code === 'ECONNREFUSED') {
      console.log("Relayer not available, falling back to direct submission...");
    } else {
      console.log(`Relayer error: ${err.response?.data?.error || err.message}`);
      console.log("Falling back to direct submission...");
    }
  }

  // ── 3. Fallback: Direct submission (requires gas) ──
  console.log("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log("Direct submission to blockchain...");

  if (!DELIVERY_REGISTRY_ADDR) {
    console.error("DELIVERY_REGISTRY_ADDRESS not set in .env");
    process.exit(1);
  }

  const provider = new ethers.JsonRpcProvider(RPC_URL);

  // Load wallet for direct submission
  const keystorePath = path.join(__dirname, "keystore", "keystore.json");
  if (!fs.existsSync(keystorePath)) {
    // Use first hardhat account for testing
    console.log("No keystore found, using test account...");
    const signer = await provider.getSigner(0);
    await submitDirect(signer, receipt);
  } else {
    const password = process.env.KEYSTORE_PW || "change_this_in_production";
    const encryptedJson = fs.readFileSync(keystorePath, "utf-8");
    console.log("Decrypting wallet...");
    let wallet = await ethers.Wallet.fromEncryptedJson(encryptedJson, password);
    wallet = wallet.connect(provider);

    // Check balance
    const balance = await provider.getBalance(wallet.address);
    if (balance === 0n) {
      console.log("Warning: Wallet has no ETH for gas.");
      console.log("Using test account instead...");
      const signer = await provider.getSigner(0);
      await submitDirect(signer, receipt);
    } else {
      await submitDirect(wallet, receipt);
    }
  }
}

async function submitDirect(signer, receipt) {
  const deliveryRegistry = new ethers.Contract(
    DELIVERY_REGISTRY_ADDR,
    DELIVERY_ABI,
    signer
  );

  console.log(`\nSubmitting to DeliveryRegistry: ${DELIVERY_REGISTRY_ADDR}`);
  console.log(`  From: ${await signer.getAddress()}`);

  try {
    const tx = await deliveryRegistry.submitReceipt(
      receipt.tradeId,
      receipt.nodeId,
      receipt.meterSnapshotHash,
      receipt.deliveredKwhBucket,
      receipt.periodStart,
      receipt.periodEnd,
      receipt.nonce,
      receipt.signature
    );

    console.log(`  TX Hash: ${tx.hash}`);
    console.log("  Waiting for confirmation...");

    const txReceipt = await tx.wait();
    console.log(`  Confirmed in block: ${txReceipt.blockNumber}`);
    console.log(`  Gas used: ${txReceipt.gasUsed}`);

    // Check receipt count
    const receiptCount = await deliveryRegistry.getReceiptCount(receipt.tradeId);
    console.log(`\n  Total receipts for trade: ${receiptCount}`);

    console.log("\nDelivery receipt submitted successfully!");

  } catch (err) {
    console.error("\nSubmission failed:", err.reason || err.message);

    // Parse common errors
    if (err.message?.includes("InvalidDeliverySignature")) {
      console.error("Hint: The signature does not match the node owner.");
    } else if (err.message?.includes("InvalidDeliveryNonce")) {
      console.error("Hint: The nonce has already been used or is incorrect.");
    } else if (err.message?.includes("ReceiptAlreadySubmitted")) {
      console.error("Hint: This node has already submitted a receipt for this trade.");
    } else if (err.message?.includes("NodeNotActive")) {
      console.error("Hint: The node is not active (may have been revoked).");
    }

    process.exit(1);
  }
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
