/**
 * relayer/server.js — Grid-Guardian Meta-Transaction Relayer
 *
 * Responsibilities:
 *  1. Accept EIP-712 signed RegisterNode payloads from Pi devices.
 *  2. Verify signature + nonce off-chain.
 *  3. Submit registerNodeMeta() tx to IdentitySC (relayer pays gas).
 *  4. Accept device attestation blobs and forward to admin for verification.
 *  5. Proxy KYC profile storage to the off-chain Postgres registry.
 *  6. Provide nonce query endpoint for Pi clients.
 *  7. Accept signed GasVouchers and claim reimbursement from CollateralSC.
 *  8. Execute generic meta-tx requests from Pi devices.
 *  9. Provide collateral/allowance query endpoints.
 *
 * Endpoints:
 *   POST /api/register       — submit signed registration
 *   POST /api/attest         — submit device attestation
 *   POST /kyc                — store KYC profile (proxied to off-chain registry)
 *   GET  /nonce/:address     — current on-chain nonce for an address
 *   GET  /health             — liveness probe
 *   POST /voucher/submit     — submit signed GasVoucher for claim
 *   POST /meta-tx/request    — submit generic meta-tx request
 *   GET  /voucher/nonce/:nodeId            — voucher nonce
 *   GET  /allowance/:nodeId/:relayer       — relayer allowance
 *   GET  /deposit/:nodeId                  — node deposit balance
 */
require("dotenv").config({ path: require("path").resolve(__dirname, "../.env") });
const express  = require("express");
const { ethers } = require("ethers");

const app  = express();
app.use(express.json());

// ── Config ─────────────────────────────────────────────────────────
const PORT              = process.env.RELAYER_PORT || 4000;
const RPC_URL           = process.env.RPC_URL || "http://127.0.0.1:8545";
const RELAYER_KEY       = process.env.RELAYER_PRIVATE_KEY;
const IDENTITY_SC_ADDR  = process.env.IDENTITY_SC_ADDRESS;
const COLLATERAL_SC_ADDR = process.env.COLLATERAL_SC_ADDRESS;
const REGISTRY_URL      = process.env.REGISTRY_URL || "http://127.0.0.1:5000";

if (!RELAYER_KEY) {
  console.error("❌  RELAYER_PRIVATE_KEY not set in .env");
  process.exit(1);
}

// ── Ethers setup ───────────────────────────────────────────────────
const provider      = new ethers.JsonRpcProvider(RPC_URL);
const relayerWallet = new ethers.Wallet(RELAYER_KEY, provider);

// ── Minimal ABI (only the functions we call) ───────────────────────
const IDENTITY_ABI = [
  "function registerNodeMeta(bytes32 nodeId, bytes32 pubkeyHash, string metaURI, uint256 nonce, uint256 expiry, bytes signature) external",
  "function registerNode(bytes32 nodeId, bytes32 pubkeyHash, string metaURI) external",
  "function attestNode(bytes32 nodeId) external",
  "function nonces(address) view returns (uint256)",
  "function nodes(bytes32) view returns (address owner, bytes32 pubkeyHash, string metaURI, uint256 stake, uint256 registeredAt, bool active, bool attested)",
  "event NodeRegistered(bytes32 indexed nodeId, address indexed owner, string metaURI, uint256 timestamp)",
];

function getContract() {
  if (!IDENTITY_SC_ADDR) {
    throw new Error("IDENTITY_SC_ADDRESS not set — deploy the contract first.");
  }
  return new ethers.Contract(IDENTITY_SC_ADDR, IDENTITY_ABI, relayerWallet);
}

// ── CollateralSC ABI (for voucher claims) ──────────────────────────
const COLLATERAL_ABI = [
  "function claimGas(tuple(bytes32 nodeId, address relayer, uint256 amount, uint256 maxGas, uint256 gasPrice, uint256 nonce, uint256 expiry, bytes32 txHash) voucher, bytes signature) external",
  "function voucherNonce(bytes32) view returns (uint256)",
  "function deposits(bytes32) view returns (uint256)",
  "function relayerAllowance(bytes32, address) view returns (uint256)",
];

function getCollateralContract() {
  if (!COLLATERAL_SC_ADDR) {
    throw new Error("COLLATERAL_SC_ADDRESS not set — deploy CollateralSC first.");
  }
  return new ethers.Contract(COLLATERAL_SC_ADDR, COLLATERAL_ABI, relayerWallet);
}

// ── EIP-712 domain (must match contract constructor params) ────────
function getDomain() {
  return {
    name:              "GridGuardian-Relayer",
    version:           "1",
    chainId:           31337,
    verifyingContract: IDENTITY_SC_ADDR,
  };
}

const EIP712_TYPES = {
  RegisterNode: [
    { name: "nodeId",     type: "bytes32" },
    { name: "pubkeyHash", type: "bytes32" },
    { name: "metaURI",    type: "string"  },
    { name: "nonce",      type: "uint256" },
    { name: "expiry",     type: "uint256" },
  ],
};

// ════════════════════════════════════════════════════════════════════
//                          ENDPOINTS
// ════════════════════════════════════════════════════════════════════

// ── Health ──
app.get("/health", (_req, res) => res.json({ status: "ok", relayer: relayerWallet.address }));

// ── Nonce ──
app.get("/nonce/:address", async (req, res) => {
  try {
    const contract = getContract();
    const nonce = await contract.nonces(req.params.address);
    res.json({ nonce: Number(nonce) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Register (meta-tx) ──
app.post("/api/register", async (req, res) => {
  try {
    const { nodeId, pubkeyHash, metaURI, nonce, expiry, signature, signer } = req.body;

    // ── Validate required fields ──
    if (!nodeId || !pubkeyHash || !metaURI || signature === undefined || !signer) {
      return res.status(400).json({ error: "Missing required fields" });
    }

    // ── Verify EIP-712 signature off-chain ──
    const message = { nodeId, pubkeyHash, metaURI, nonce, expiry };
    const recovered = ethers.verifyTypedData(getDomain(), EIP712_TYPES, message, signature);

    if (recovered.toLowerCase() !== signer.toLowerCase()) {
      return res.status(403).json({
        error: "Invalid signature",
        expected: signer,
        recovered,
      });
    }
    console.log(`✓ Signature verified for ${signer}`);

    // ── Submit on-chain ──
    const contract = getContract();
    const tx = await contract.registerNodeMeta(
      nodeId,
      pubkeyHash,
      metaURI,
      nonce,
      expiry,
      signature
    );
    const receipt = await tx.wait();

    console.log(`✅ Node registered — tx: ${receipt.hash}`);
    res.json({
      success: true,
      txHash:  receipt.hash,
      nodeId,
      owner:   signer,
    });
  } catch (err) {
    console.error("Registration error:", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ── Device attestation ──
app.post("/api/attest", async (req, res) => {
  try {
    const { attestation, signature, signer } = req.body;

    if (!attestation || !signature || !signer) {
      return res.status(400).json({ error: "Missing required fields" });
    }

    // ── Verify attestation signature ──
    const attestHash = ethers.keccak256(
      ethers.toUtf8Bytes(JSON.stringify(attestation))
    );
    const recovered = ethers.verifyMessage(ethers.getBytes(attestHash), signature);

    if (recovered.toLowerCase() !== signer.toLowerCase()) {
      return res.status(403).json({ error: "Invalid attestation signature" });
    }

    console.log(`✓ Attestation verified for ${signer} (nodeId: ${attestation.nodeId})`);

    // ── Mark attested on-chain (admin action) ──
    const contract = getContract();
    const tx = await contract.attestNode(attestation.nodeId);
    await tx.wait();

    console.log(`✅ Node attested on-chain: ${attestation.nodeId}`);
    res.json({ success: true, nodeId: attestation.nodeId, attested: true });
  } catch (err) {
    console.error("Attestation error:", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ── KYC proxy (forwards to off-chain registry) ──
app.post("/kyc", async (req, res) => {
  try {
    // If registry is running, forward to it
    const axios = require("axios");
    const resp  = await axios.post(`${REGISTRY_URL}/kyc`, req.body);
    res.json(resp.data);
  } catch (err) {
    // Fallback: return a hash-based metaURI
    const profileHash = ethers.keccak256(
      ethers.toUtf8Bytes(JSON.stringify(req.body))
    );
    const metaURI = `ipfs://${profileHash.slice(2)}`;
    console.log(`⚠  Registry unavailable — returning fallback metaURI: ${metaURI}`);
    res.json({ metaURI, stored: false });
  }
});

// ════════════════════════════════════════════════════════════════════
//          STEP 2: COLLATERAL & GAS VOUCHER ENDPOINTS
// ════════════════════════════════════════════════════════════════════

// ── Voucher nonce ──
app.get("/voucher/nonce/:nodeId", async (req, res) => {
  try {
    const contract = getCollateralContract();
    const nonce = await contract.voucherNonce(req.params.nodeId);
    res.json({ nonce: Number(nonce) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Deposit balance ──
app.get("/deposit/:nodeId", async (req, res) => {
  try {
    const contract = getCollateralContract();
    const balance = await contract.deposits(req.params.nodeId);
    res.json({ deposit: balance.toString() });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Relayer allowance ──
app.get("/allowance/:nodeId/:relayer", async (req, res) => {
  try {
    const contract = getCollateralContract();
    const allowance = await contract.relayerAllowance(req.params.nodeId, req.params.relayer);
    res.json({ allowance: allowance.toString() });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Submit signed GasVoucher for claim ──
app.post("/voucher/submit", async (req, res) => {
  try {
    const { voucher, signature, signer } = req.body;
    if (!voucher || !signature) {
      return res.status(400).json({ error: "Missing voucher or signature" });
    }

    console.log(`📝 GasVoucher received from ${signer} — amount: ${voucher.amount}, nonce: ${voucher.nonce}`);

    // Verify voucher.relayer matches this relayer
    if (voucher.relayer.toLowerCase() !== relayerWallet.address.toLowerCase()) {
      return res.status(400).json({ error: "Voucher relayer does not match this relayer" });
    }

    // Submit claimGas on-chain
    const contract = getCollateralContract();
    const voucherTuple = [
      voucher.nodeId,
      voucher.relayer,
      voucher.amount,
      voucher.maxGas,
      voucher.gasPrice,
      voucher.nonce,
      voucher.expiry,
      voucher.txHash,
    ];
    const tx = await contract.claimGas(voucherTuple, signature);
    const receipt = await tx.wait();

    console.log(`✅ GasClaimed — tx: ${receipt.hash}, amount: ${voucher.amount}`);
    res.json({
      success: true,
      txHash:  receipt.hash,
      amount:  voucher.amount,
      nonce:   voucher.nonce,
    });
  } catch (err) {
    console.error("Voucher claim error:", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ── Generic meta-tx request (Pi asks relayer to execute a tx) ──
app.post("/meta-tx/request", async (req, res) => {
  try {
    const { target, calldata, calldataHash, maxGas, signer, signature, nodeId } = req.body;

    if (!target || !calldata || !signature || !signer) {
      return res.status(400).json({ error: "Missing required fields" });
    }

    // Verify signature (simple message hash)
    const requestHash = ethers.keccak256(
      ethers.toUtf8Bytes(JSON.stringify({
        target: req.body.target,
        calldataHash: req.body.calldataHash,
        maxGas: req.body.maxGas,
        maxGasPrice: req.body.maxGasPrice,
        nonce: req.body.nonce,
        expiry: req.body.expiry,
      }))
    );
    const recovered = ethers.verifyMessage(ethers.getBytes(requestHash), signature);
    if (recovered.toLowerCase() !== signer.toLowerCase()) {
      return res.status(403).json({ error: "Invalid meta-tx signature" });
    }

    console.log(`📝 Meta-tx request from ${signer} to ${target}`);

    // Execute the call as relayer
    const tx = await relayerWallet.sendTransaction({
      to:       target,
      data:     calldata,
      gasLimit: maxGas || 300000,
    });
    const receipt = await tx.wait();

    console.log(`✅ Meta-tx executed — tx: ${receipt.hash}, gasUsed: ${receipt.gasUsed}`);
    res.json({
      success:  true,
      txHash:   receipt.hash,
      gasUsed:  receipt.gasUsed.toString(),
      gasPrice: receipt.gasPrice.toString(),
    });
  } catch (err) {
    console.error("Meta-tx error:", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ── Start ──
app.listen(PORT, () => {
  console.log(`\n🚀  Grid-Guardian Relayer listening on port ${PORT}`);
  console.log(`   Relayer account:  ${relayerWallet.address}`);
  console.log(`   IdentitySC:       ${IDENTITY_SC_ADDR || "(not set — deploy first)"}`);
  console.log(`   CollateralSC:     ${COLLATERAL_SC_ADDR || "(not set — deploy first)"}`);
  console.log(`   RPC:              ${RPC_URL}\n`);
});
