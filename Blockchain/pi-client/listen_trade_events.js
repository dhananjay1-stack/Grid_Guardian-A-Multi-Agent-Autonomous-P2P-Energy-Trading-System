/**
 * pi-client/listen_trade_events.js
 *
 * Listen for TradeExecuted events from SettlementSC.
 * When a trade is proposed that involves this node, trigger actuator
 * and prepare for delivery reporting.
 *
 * Usage:
 *   node listen_trade_events.js
 *
 * Environment:
 *   - RPC_URL: WebSocket or HTTP RPC endpoint
 *   - SETTLEMENT_SC_ADDRESS: Deployed SettlementSC address
 *   - NODE_ID: This node's registered node ID (bytes32)
 */
require("dotenv").config();
const { ethers } = require("ethers");
const fs = require("fs");
const path = require("path");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL = process.env.RPC_URL || "http://127.0.0.1:8545";
const SETTLEMENT_SC_ADDR = process.env.SETTLEMENT_SC_ADDRESS;
const KEYSTORE_DIR = path.join(__dirname, "keystore");
const SALT_PATH = path.join(KEYSTORE_DIR, "device_salt.bin");
const KEYSTORE_PATH = path.join(KEYSTORE_DIR, "keystore.json");

// SettlementSC ABI (just the events we need)
const SETTLEMENT_ABI = [
  "event TradeProposed(bytes32 indexed tradeId, bytes32 indexed matchHash, bytes32 buyerNodeId, bytes32 sellerNodeId, uint16 kwhBucket, uint16 priceBucket, uint256 lockedAmount)",
  "event TradeExecuted(bytes32 indexed tradeId, bytes32 indexed buyerNodeId, bytes32 indexed sellerNodeId, uint16 kwhBucket, uint16 priceBucket)",
  "event DeliveryMarked(bytes32 indexed tradeId, uint256 deliveredBlock)",
  "event SettlementCompleted(bytes32 indexed tradeId, uint256 amount)",
];

async function main() {
  if (!SETTLEMENT_SC_ADDR) {
    console.error("SETTLEMENT_SC_ADDRESS not set in .env");
    process.exit(1);
  }

  // Load wallet to get nodeId
  let nodeId;
  if (fs.existsSync(KEYSTORE_PATH) && fs.existsSync(SALT_PATH)) {
    const password = process.env.KEYSTORE_PW || "change_this_in_production";
    const encryptedJson = fs.readFileSync(KEYSTORE_PATH, "utf-8");
    const wallet = await ethers.Wallet.fromEncryptedJson(encryptedJson, password);
    const salt = fs.readFileSync(SALT_PATH);
    const pubkeyBytes = ethers.getBytes(wallet.signingKey.compressedPublicKey);
    nodeId = ethers.keccak256(ethers.concat([pubkeyBytes, salt]));
    console.log("This node ID:", nodeId);
  } else if (process.env.NODE_ID) {
    nodeId = process.env.NODE_ID;
    console.log("Using NODE_ID from env:", nodeId);
  } else {
    console.error("Could not determine node ID. Run generate_keys.js or set NODE_ID in .env");
    process.exit(1);
  }

  console.log(`\nConnecting to ${RPC_URL}...`);
  const provider = new ethers.JsonRpcProvider(RPC_URL);

  const settlement = new ethers.Contract(SETTLEMENT_SC_ADDR, SETTLEMENT_ABI, provider);
  console.log(`Listening for events on SettlementSC: ${SETTLEMENT_SC_ADDR}\n`);

  // Listen for TradeExecuted events
  settlement.on("TradeExecuted", async (tradeId, buyerNodeId, sellerNodeId, kwhBucket, priceBucket, event) => {
    console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    console.log("TradeExecuted event received!");
    console.log(`  Trade ID     : ${tradeId}`);
    console.log(`  Buyer Node   : ${buyerNodeId}`);
    console.log(`  Seller Node  : ${sellerNodeId}`);
    console.log(`  kWh Bucket   : ${kwhBucket}`);
    console.log(`  Price Bucket : ${priceBucket}`);
    console.log(`  Block        : ${event.log.blockNumber}`);

    // Check if this trade involves our node
    if (buyerNodeId.toLowerCase() === nodeId.toLowerCase()) {
      console.log("\n  >> This node is the BUYER - prepare to receive energy");
      triggerBuyerActuation(tradeId, kwhBucket);
    } else if (sellerNodeId.toLowerCase() === nodeId.toLowerCase()) {
      console.log("\n  >> This node is the SELLER - prepare to deliver energy");
      triggerSellerActuation(tradeId, kwhBucket);
    } else {
      console.log("\n  >> Trade does not involve this node");
    }
    console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");
  });

  // Listen for DeliveryMarked events
  settlement.on("DeliveryMarked", (tradeId, deliveredBlock, event) => {
    console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    console.log("DeliveryMarked event received!");
    console.log(`  Trade ID       : ${tradeId}`);
    console.log(`  Delivered Block: ${deliveredBlock}`);
    console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");
  });

  // Listen for SettlementCompleted events
  settlement.on("SettlementCompleted", (tradeId, amount, event) => {
    console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    console.log("SettlementCompleted event received!");
    console.log(`  Trade ID : ${tradeId}`);
    console.log(`  Amount   : ${amount}`);
    console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");
  });

  console.log("Event listener started. Waiting for trades...\n");
  console.log("Press Ctrl+C to exit.\n");
}

// Placeholder for actuator control (buyer side)
function triggerBuyerActuation(tradeId, kwhBucket) {
  console.log("  [ACTUATOR] Buyer actuation triggered");
  console.log("  [ACTUATOR] Preparing to receive energy from grid");
  // In production: send command to energy management system
  // Example: enable battery charging, adjust load, etc.
}

// Placeholder for actuator control (seller side)
function triggerSellerActuation(tradeId, kwhBucket) {
  console.log("  [ACTUATOR] Seller actuation triggered");
  console.log("  [ACTUATOR] Preparing to deliver energy to grid");
  // In production: enable solar inverter export, battery discharge, etc.

  // After delivery period, the seller should:
  // 1. Read meter values
  // 2. Sign a delivery receipt (see sign_delivery_receipt.js)
  // 3. Submit via relayer (see submit_delivery.js)
  console.log("  [TODO] After delivery, run: node sign_delivery_receipt.js <tradeId>");
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
