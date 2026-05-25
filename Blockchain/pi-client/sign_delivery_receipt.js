/**
 * pi-client/sign_delivery_receipt.js
 *
 * Sign a DeliveryReceipt (EIP-712) after energy delivery.
 * The signature proves the Pi node attests to the delivery.
 *
 * Usage:
 *   node sign_delivery_receipt.js <tradeId> [kwhBucket]
 *
 * Environment:
 *   - DELIVERY_REGISTRY_ADDRESS: Deployed DeliveryRegistry address
 *   - CHAIN_ID: Chain ID (default 31337 for Hardhat)
 *   - KEYSTORE_PW: Password for encrypted keystore
 */
require("dotenv").config();
const { ethers } = require("ethers");
const fs = require("fs");
const path = require("path");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL = process.env.RPC_URL || "http://127.0.0.1:8545";
const DELIVERY_REGISTRY_ADDR = process.env.DELIVERY_REGISTRY_ADDRESS;
const CHAIN_ID = parseInt(process.env.CHAIN_ID || "31337", 10);
const KEYSTORE_DIR = path.join(__dirname, "keystore");
const KEYSTORE_PATH = path.join(KEYSTORE_DIR, "keystore.json");
const SALT_PATH = path.join(KEYSTORE_DIR, "device_salt.bin");
const RECEIPTS_DIR = path.join(__dirname, "receipts");

// EIP-712 types for DeliveryReceipt
const DELIVERY_TYPES = {
  DeliveryReceipt: [
    { name: "tradeId", type: "bytes32" },
    { name: "nodeId", type: "bytes32" },
    { name: "meterSnapshotHash", type: "bytes32" },
    { name: "deliveredKwhBucket", type: "uint16" },
    { name: "periodStart", type: "uint256" },
    { name: "periodEnd", type: "uint256" },
    { name: "nonce", type: "uint256" },
  ],
};

// DeliveryRegistry ABI (minimal)
const DELIVERY_ABI = [
  "function getDeliveryNonce(bytes32 nodeId) view returns (uint256)",
];

async function main() {
  const args = process.argv.slice(2);
  if (args.length < 1) {
    console.log("Usage: node sign_delivery_receipt.js <tradeId> [kwhBucket]");
    console.log("  tradeId   : bytes32 trade identifier");
    console.log("  kwhBucket : delivered kWh bucket (default: 10)");
    process.exit(1);
  }

  const tradeId = args[0];
  const kwhBucket = parseInt(args[1] || "10", 10);

  if (!DELIVERY_REGISTRY_ADDR) {
    console.error("DELIVERY_REGISTRY_ADDRESS not set in .env");
    process.exit(1);
  }

  // ── 1. Load wallet ──
  if (!fs.existsSync(KEYSTORE_PATH)) {
    console.error("Keystore not found. Run `node generate_keys.js` first.");
    process.exit(1);
  }
  const password = process.env.KEYSTORE_PW || "change_this_in_production";
  const encryptedJson = fs.readFileSync(KEYSTORE_PATH, "utf-8");

  console.log("Decrypting wallet...");
  const wallet = await ethers.Wallet.fromEncryptedJson(encryptedJson, password);
  console.log("  Address:", wallet.address);

  // ── 2. Compute nodeId ──
  const salt = fs.readFileSync(SALT_PATH);
  const pubkeyBytes = ethers.getBytes(wallet.signingKey.compressedPublicKey);
  const nodeId = ethers.keccak256(ethers.concat([pubkeyBytes, salt]));
  console.log("  nodeId:", nodeId);

  // ── 3. Read meter (simulated) ──
  // In production: read actual smart meter values via Modbus/GPIO
  const meterReading = Math.floor(Math.random() * 1000000);
  const meterTimestamp = Math.floor(Date.now() / 1000);
  const meterSnapshotHash = ethers.keccak256(
    ethers.solidityPacked(
      ["uint256", "uint256", "bytes32"],
      [meterReading, meterTimestamp, nodeId]
    )
  );
  console.log("\nMeter reading (simulated):");
  console.log(`  Reading   : ${meterReading} Wh`);
  console.log(`  Timestamp : ${new Date(meterTimestamp * 1000).toISOString()}`);
  console.log(`  Snapshot  : ${meterSnapshotHash}`);

  // ── 4. Fetch nonce from DeliveryRegistry ──
  console.log("\nFetching delivery nonce...");
  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const deliveryRegistry = new ethers.Contract(DELIVERY_REGISTRY_ADDR, DELIVERY_ABI, provider);

  let nonce;
  try {
    nonce = await deliveryRegistry.getDeliveryNonce(nodeId);
    console.log(`  Nonce: ${nonce}`);
  } catch (err) {
    console.log("  Warning: Could not fetch nonce, using 0");
    nonce = 0n;
  }

  // ── 5. Build delivery period ──
  const periodStart = meterTimestamp - 3600; // 1 hour ago
  const periodEnd = meterTimestamp;

  // ── 6. Build EIP-712 domain and message ──
  const domain = {
    name: "GridGuardian-Delivery",
    version: "1",
    chainId: CHAIN_ID,
    verifyingContract: DELIVERY_REGISTRY_ADDR,
  };

  const message = {
    tradeId,
    nodeId,
    meterSnapshotHash,
    deliveredKwhBucket: kwhBucket,
    periodStart,
    periodEnd,
    nonce,
  };

  console.log("\nSigning delivery receipt...");
  console.log("  Domain:", JSON.stringify(domain, null, 2));
  console.log("  Message:", JSON.stringify(message, (k, v) =>
    typeof v === 'bigint' ? v.toString() : v, 2));

  // ── 7. Sign ──
  const signature = await wallet.signTypedData(domain, DELIVERY_TYPES, message);
  console.log("\nSignature:", signature);

  // ── 8. Save receipt to file ──
  if (!fs.existsSync(RECEIPTS_DIR)) {
    fs.mkdirSync(RECEIPTS_DIR, { recursive: true });
  }

  const receipt = {
    tradeId,
    nodeId,
    meterSnapshotHash,
    deliveredKwhBucket: kwhBucket,
    periodStart,
    periodEnd,
    nonce: nonce.toString(),
    signature,
    signer: wallet.address,
    signedAt: new Date().toISOString(),
  };

  const receiptPath = path.join(RECEIPTS_DIR, `receipt_${tradeId.slice(0, 18)}.json`);
  fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2));
  console.log(`\nReceipt saved to: ${receiptPath}`);

  console.log("\nNext step: Submit receipt via relayer:");
  console.log(`  node submit_delivery.js ${receiptPath}`);
}

main().catch((err) => {
  console.error("Error signing delivery receipt:", err);
  process.exit(1);
});
