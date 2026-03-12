/**
 * pi-client/register_via_relayer.js
 *
 * Full registration flow:
 *   1. Load wallet from encrypted keystore.
 *   2. Load device salt and compute nodeId / pubkeyHash.
 *   3. Create node profile JSON, upload to IPFS → get metaURI (CID).
 *   4. Sign an EIP-712 RegisterNode message.
 *   5. POST the signed payload to the relayer.
 *   6. The relayer submits the meta-tx on-chain (Pi pays zero gas).
 */
require("dotenv").config();
const { ethers } = require("ethers");
const axios  = require("axios");
const fs     = require("fs");
const path   = require("path");
const crypto = require("crypto");

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
  const password     = process.env.KEYSTORE_PW || "change_this_in_production";
  const encryptedJson = fs.readFileSync(KEYSTORE_PATH, "utf-8");

  console.log("Decrypting wallet...");
  const wallet = await ethers.Wallet.fromEncryptedJson(encryptedJson, password);
  console.log("  Address:", wallet.address);

  // ── 2. Compute nodeId & pubkeyHash ──
  const salt       = fs.readFileSync(SALT_PATH);
  const pubkeyBytes = ethers.getBytes(wallet.signingKey.compressedPublicKey);
  const nodeId      = ethers.keccak256(ethers.concat([pubkeyBytes, salt]));
  const pubkeyHash  = ethers.keccak256(pubkeyBytes);

  console.log("  nodeId    :", nodeId);
  console.log("  pubkeyHash:", pubkeyHash);

  // ── 3. Build & upload profile to IPFS (via relayer proxy) ──
  //    In production, post encrypted JSON to local IPFS or Pinata.
  //    Here we post plain JSON to relayer which stores KYC off-chain
  //    and pins the metaURI to IPFS for us.
  const profile = {
    household_id:  wallet.address,
    archetype:     "prosumer",
    device_serial: crypto.randomBytes(8).toString("hex"),
    firmware_hash: ethers.keccak256(ethers.toUtf8Bytes("grid-guardian-fw-v1.0")),
    registered_at: new Date().toISOString(),
  };

  let metaURI;
  try {
    const kycResp = await axios.post(`${RELAYER_URL}/kyc`, profile);
    metaURI = kycResp.data.metaURI;
    console.log("  metaURI   :", metaURI);
  } catch (err) {
    // Fallback: use a hash-based metaURI if IPFS is down
    metaURI = "ipfs://" + ethers.keccak256(ethers.toUtf8Bytes(JSON.stringify(profile))).slice(2);
    console.log("  metaURI (fallback hash):", metaURI);
  }

  // ── 4. Fetch nonce from relayer ──
  let nonce = 0;
  try {
    const nonceResp = await axios.get(`${RELAYER_URL}/nonce/${wallet.address}`);
    nonce = nonceResp.data.nonce;
  } catch {
    console.log("  Using nonce = 0 (relayer nonce endpoint unavailable)");
  }

  const expiry = Math.floor(Date.now() / 1000) + 3600; // valid for 1 hour

  // ── 5. Sign EIP-712 typed data ──
  const domain = {
    name:              "GridGuardian-Relayer",
    version:           "1",
    chainId:           CHAIN_ID,
    verifyingContract: IDENTITY_SC_ADDR,
  };

  const types = {
    RegisterNode: [
      { name: "nodeId",     type: "bytes32" },
      { name: "pubkeyHash", type: "bytes32" },
      { name: "metaURI",    type: "string"  },
      { name: "nonce",      type: "uint256" },
      { name: "expiry",     type: "uint256" },
    ],
  };

  const message = {
    nodeId,
    pubkeyHash,
    metaURI,
    nonce,
    expiry,
  };

  console.log("\nSigning EIP-712 message...");
  const signature = await wallet.signTypedData(domain, types, message);
  console.log("  Signature:", signature);

  // ── 6. Submit to relayer ──
  const payload = { ...message, signature, signer: wallet.address };

  console.log("\nSending to relayer...");
  try {
    const resp = await axios.post(`${RELAYER_URL}/api/register`, payload);
    console.log("✅  Relayer response:", resp.data);
  } catch (err) {
    console.error("❌  Relayer call failed:", err.response?.data || err.message);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("Registration failed:", err);
  process.exit(1);
});
