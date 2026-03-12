/**
 * pi-client/generate_keys.js
 *
 * Generates a secp256k1 keypair for the Pi edge node.
 *
 * Flow A — Hardware secure element (ATECC608A / TPM):
 *   In production, the private key is generated AND stored inside the
 *   secure element. Only the public key is exported. This script would
 *   call the ATECC608A provisioning library (e.g. via pkcs11 or
 *   microchip-atecc-util). We simulate that path here.
 *
 * Flow B — Software encrypted keystore (fallback):
 *   ethers.Wallet.createRandom() → encrypt → store as JSON keystore
 *   with POSIX 600 permissions.
 *
 * Outputs:
 *   keystore/keystore.json   — encrypted JSON wallet (mode 0o600)
 *   keystore/device_salt.bin — 32-byte random device salt (mode 0o600)
 *   stdout: address, publicKey, nodeId (keccak256(pubkey || salt))
 */
require("dotenv").config();
const { ethers } = require("ethers");
const crypto = require("crypto");
const fs   = require("fs");
const path = require("path");

const KEYSTORE_DIR = path.join(__dirname, "keystore");

async function main() {
  // ── Ensure keystore directory exists ──
  if (!fs.existsSync(KEYSTORE_DIR)) {
    fs.mkdirSync(KEYSTORE_DIR, { recursive: true });
  }

  // ═══════════════════════════════════════════════════════════════════
  //  Flow A — Secure element simulation
  // ═══════════════════════════════════════════════════════════════════
  // In production:
  //   const pubkey = secureElement.generateKeyAndReturnPublicKey();
  //   Private key NEVER leaves the chip.
  //
  // We simulate by generating a normal wallet and treating its
  // public key as "exported from HW".
  console.log("──── Flow A: Secure element (simulated) ────");
  console.log("  In production the private key stays in ATECC608A/TPM.");
  console.log("  Here we simulate by creating a software wallet.\n");

  // ═══════════════════════════════════════════════════════════════════
  //  Flow B — Software encrypted keystore (actual implementation)
  // ═══════════════════════════════════════════════════════════════════
  const wallet = ethers.Wallet.createRandom();
  const password = process.env.KEYSTORE_PW || "change_this_in_production";

  console.log("Encrypting wallet (this may take a few seconds)...");
  const encryptedJson = await wallet.encrypt(password);

  // ── Write keystore (mode 600 = owner read/write only) ──
  const keystorePath = path.join(KEYSTORE_DIR, "keystore.json");
  fs.writeFileSync(keystorePath, encryptedJson, { mode: 0o600 });
  console.log(`✅  Keystore written → ${keystorePath}`);

  // ── Generate device salt (32 bytes) ──
  const salt = crypto.randomBytes(32);
  const saltPath = path.join(KEYSTORE_DIR, "device_salt.bin");
  fs.writeFileSync(saltPath, salt, { mode: 0o600 });
  console.log(`✅  Device salt written → ${saltPath}`);

  // ── Compute nodeId = keccak256(pubkey || salt) ──
  //    We use the compressed public key (33 bytes) for deterministic ID.
  const pubkeyBytes = ethers.getBytes(wallet.signingKey.compressedPublicKey);
  const combined    = ethers.concat([pubkeyBytes, salt]);
  const nodeId      = ethers.keccak256(combined);

  // ── Compute pubkeyHash = keccak256(pubkey) ──
  const pubkeyHash = ethers.keccak256(pubkeyBytes);

  console.log("\n──── Device Identity ────");
  console.log("  Address   :", wallet.address);
  console.log("  Public Key:", wallet.signingKey.compressedPublicKey);
  console.log("  nodeId    :", nodeId);
  console.log("  pubkeyHash:", pubkeyHash);
  console.log("\n💡  Save the nodeId — you will need it for registration.");
}

main().catch((err) => {
  console.error("Key generation failed:", err);
  process.exit(1);
});
