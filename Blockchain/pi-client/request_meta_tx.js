/**
 * pi-client/request_meta_tx.js
 *
 * Pi crafts a meta-tx request describing an intended on-chain action,
 * signs it via EIP-712, and sends it to the relayer for execution.
 *
 * After the relayer executes, it returns txHash + gasUsed.
 * The Pi then signs a GasVoucher for reimbursement (see sign_gas_voucher.js).
 *
 * Usage:
 *   node request_meta_tx.js
 */
require("dotenv").config();
const { ethers } = require("ethers");
const axios  = require("axios");
const fs     = require("fs");
const path   = require("path");

// ── Config ─────────────────────────────────────────────────────────
const RELAYER_URL        = process.env.RELAYER_URL || "http://127.0.0.1:4000";
const IDENTITY_SC_ADDR   = process.env.IDENTITY_SC_ADDRESS;
const CHAIN_ID           = parseInt(process.env.CHAIN_ID || "31337", 10);
const KEYSTORE_DIR       = path.join(__dirname, "keystore");
const KEYSTORE_PATH      = path.join(KEYSTORE_DIR, "keystore.json");
const SALT_PATH          = path.join(KEYSTORE_DIR, "device_salt.bin");

async function main() {
  // ── 1. Load wallet ──
  if (!fs.existsSync(KEYSTORE_PATH)) {
    console.error("❌  Keystore not found. Run `node generate_keys.js` first.");
    process.exit(1);
  }
  const password      = process.env.KEYSTORE_PW || "change_this_in_production";
  const encryptedJson = fs.readFileSync(KEYSTORE_PATH, "utf-8");
  console.log("Decrypting wallet...");
  const wallet = await ethers.Wallet.fromEncryptedJson(encryptedJson, password);
  console.log("  Address:", wallet.address);

  // ── 2. Compute nodeId & pubkeyHash ──
  const salt        = fs.readFileSync(SALT_PATH);
  const pubkeyBytes = ethers.getBytes(wallet.signingKey.compressedPublicKey);
  const nodeId      = ethers.keccak256(ethers.concat([pubkeyBytes, salt]));
  const pubkeyHash  = ethers.keccak256(pubkeyBytes);

  // ── 3. Build the meta-tx request ──
  // This example requests an updateMetaURI call via the relayer.
  // In production, the Pi would request any supported on-chain action.
  const targetContract = IDENTITY_SC_ADDR;
  const iface = new ethers.Interface([
    "function updateMetaURI(bytes32 nodeId, string newMetaURI)",
  ]);
  const newMetaURI = `ipfs://${ethers.keccak256(ethers.toUtf8Bytes("updated-profile-" + Date.now())).slice(2)}`;
  const calldata = iface.encodeFunctionData("updateMetaURI", [nodeId, newMetaURI]);
  const calldataHash = ethers.keccak256(calldata);

  const request = {
    target:       targetContract,
    calldataHash: calldataHash,
    calldata:     calldata,
    maxGas:       200000,
    maxGasPrice:  "20000000000",
    nonce:        Date.now(),           // application-level nonce
    expiry:       Math.floor(Date.now() / 1000) + 3600,
    signer:       wallet.address,
    nodeId:       nodeId,
  };

  // ── 4. Sign the request (simple message hash for now) ──
  const requestHash = ethers.keccak256(
    ethers.toUtf8Bytes(JSON.stringify({
      target: request.target,
      calldataHash: request.calldataHash,
      maxGas: request.maxGas,
      maxGasPrice: request.maxGasPrice,
      nonce: request.nonce,
      expiry: request.expiry,
    }))
  );
  const signature = await wallet.signMessage(ethers.getBytes(requestHash));

  // ── 5. Send to relayer ──
  console.log("\nSending meta-tx request to relayer...");
  console.log(`  Target: ${targetContract}`);
  console.log(`  Action: updateMetaURI`);

  try {
    const resp = await axios.post(`${RELAYER_URL}/meta-tx/request`, {
      ...request,
      signature,
    });
    console.log("✅  Relayer response:", resp.data);

    if (resp.data.txHash) {
      console.log(`\n💡  Now sign a gas voucher for reimbursement:`);
      console.log(`    node sign_gas_voucher.js --txHash ${resp.data.txHash} --gasUsed ${resp.data.gasUsed || 100000} --gasPrice ${resp.data.gasPrice || 20000000000}`);
    }
  } catch (err) {
    console.error("❌  Meta-tx request failed:", err.response?.data || err.message);
  }
}

main().catch((err) => {
  console.error("Meta-tx request failed:", err);
  process.exit(1);
});
