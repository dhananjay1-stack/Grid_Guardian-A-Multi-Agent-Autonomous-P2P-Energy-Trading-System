/**
 * pi-client/device_attestation.js
 *
 * Device attestation stub.
 *
 * Flow A — Secure element (ATECC608A):
 *   Query device certificate from ATECC, package as DER blob, sign with
 *   device key. Send attestation to admin/relayer for verification.
 *
 * Flow B — Software fallback (simulated):
 *   Generate an attestation JSON containing hwSerial + firmwareHash,
 *   sign it with the device key, and POST to the relayer for manual
 *   verification.
 */
require("dotenv").config();
const { ethers } = require("ethers");
const axios  = require("axios");
const crypto = require("crypto");
const fs     = require("fs");
const path   = require("path");
const os     = require("os");

const RELAYER_URL   = process.env.RELAYER_URL || "http://127.0.0.1:4000";
const KEYSTORE_PATH = path.join(__dirname, "keystore", "keystore.json");
const SALT_PATH     = path.join(__dirname, "keystore", "device_salt.bin");

async function main() {
  // ── Load wallet ──
  const password      = process.env.KEYSTORE_PW || "change_this_in_production";
  const encryptedJson = fs.readFileSync(KEYSTORE_PATH, "utf-8");
  const wallet        = await ethers.Wallet.fromEncryptedJson(encryptedJson, password);

  // ── Compute nodeId ──
  const salt       = fs.readFileSync(SALT_PATH);
  const pubkeyBytes = ethers.getBytes(wallet.signingKey.compressedPublicKey);
  const nodeId      = ethers.keccak256(ethers.concat([pubkeyBytes, salt]));

  // ── Build attestation object ──
  // In production: query ATECC608A for device certificate (DER) and
  // include it as attestationBlob. Here we simulate with system info.
  const attestation = {
    nodeId,
    hwSerial:      crypto.randomBytes(16).toString("hex"),  // real: read from /proc/cpuinfo on Pi
    firmwareHash:  ethers.keccak256(ethers.toUtf8Bytes("grid-guardian-fw-v1.0")),
    hostname:      os.hostname(),
    platform:      os.platform(),
    arch:          os.arch(),
    timestamp:     Math.floor(Date.now() / 1000),
  };

  // ── Sign attestation ──
  const attestHash = ethers.keccak256(
    ethers.toUtf8Bytes(JSON.stringify(attestation))
  );
  const signature = await wallet.signMessage(ethers.getBytes(attestHash));

  const payload = { attestation, signature, signer: wallet.address };

  console.log("──── Device Attestation ────");
  console.log("  nodeId     :", nodeId);
  console.log("  hwSerial   :", attestation.hwSerial);
  console.log("  firmwareHash:", attestation.firmwareHash);
  console.log("  signature  :", signature);

  // ── POST to relayer/admin for verification ──
  try {
    const resp = await axios.post(`${RELAYER_URL}/api/attest`, payload);
    console.log("✅  Attestation accepted:", resp.data);
  } catch (err) {
    console.error("❌  Attestation rejected:", err.response?.data || err.message);
  }
}

main().catch((err) => {
  console.error("Attestation failed:", err);
  process.exit(1);
});
