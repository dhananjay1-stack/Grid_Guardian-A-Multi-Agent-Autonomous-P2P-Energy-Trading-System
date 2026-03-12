/**
 * pi-client/sign_gas_voucher.js
 *
 * After the relayer executes a meta-tx, the Pi signs a GasVoucher
 * to authorize reimbursement from its CollateralSC deposit.
 *
 * Flow:
 *   1. Load wallet from encrypted keystore.
 *   2. Receive txHash + gasUsed from relayer (or command line args).
 *   3. Compute reimbursement amount (gasUsed * gasPrice * feeMultiplier).
 *   4. Sign EIP-712 GasVoucher with correct nonce from CollateralSC.
 *   5. POST signed voucher to relayer for on-chain claim.
 *
 * Usage:
 *   node sign_gas_voucher.js --txHash 0x... --gasUsed 50000 --gasPrice 20000000000
 */
require("dotenv").config();
const { ethers } = require("ethers");
const axios  = require("axios");
const fs     = require("fs");
const path   = require("path");

// ── Config ─────────────────────────────────────────────────────────
const RELAYER_URL          = process.env.RELAYER_URL || "http://127.0.0.1:4000";
const COLLATERAL_SC_ADDR   = process.env.COLLATERAL_SC_ADDRESS;
const RPC_URL              = process.env.RPC_URL || "http://127.0.0.1:8545";
const CHAIN_ID             = parseInt(process.env.CHAIN_ID || "31337", 10);
const KEYSTORE_DIR         = path.join(__dirname, "keystore");
const KEYSTORE_PATH        = path.join(KEYSTORE_DIR, "keystore.json");
const SALT_PATH            = path.join(KEYSTORE_DIR, "device_salt.bin");

// ── Parse CLI args ─────────────────────────────────────────────────
function parseArgs() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i].replace("--", "");
    args[key] = process.argv[i + 1];
  }
  return args;
}

// ── Minimal CollateralSC ABI ──────────────────────────────────────
const COLLATERAL_ABI = [
  "function voucherNonce(bytes32) view returns (uint256)",
  "function deposits(bytes32) view returns (uint256)",
  "function relayerAllowance(bytes32, address) view returns (uint256)",
];

async function main() {
  const cliArgs = parseArgs();

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

  // ── 2. Compute nodeId ──
  const salt        = fs.readFileSync(SALT_PATH);
  const pubkeyBytes = ethers.getBytes(wallet.signingKey.compressedPublicKey);
  const nodeId      = ethers.keccak256(ethers.concat([pubkeyBytes, salt]));
  console.log("  nodeId:", nodeId);

  // ── 3. Get relayer address ──
  const relayerAddr = cliArgs.relayer || process.env.RELAYER_ADDRESS;
  if (!relayerAddr) {
    console.error("❌  Provide --relayer <address> or set RELAYER_ADDRESS in .env");
    process.exit(1);
  }

  // ── 4. Get voucher nonce from contract ──
  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const collateral = new ethers.Contract(COLLATERAL_SC_ADDR, COLLATERAL_ABI, provider);
  const nonce = Number(await collateral.voucherNonce(nodeId));

  // ── 5. Compute reimbursement amount ──
  const gasUsed  = BigInt(cliArgs.gasUsed  || "100000");
  const gasPrice = BigInt(cliArgs.gasPrice || "20000000000"); // 20 gwei
  // Convert from ETH gas cost to stablecoin (6 decimals). Assume 1 ETH ≈ 2000 USDC for dev.
  const ethCost  = gasUsed * gasPrice; // in wei
  const feeMultiplier = 102n; // 2% relayer fee
  const amount = (ethCost * 2000n * feeMultiplier) / (10n ** 12n * 100n); // USDC with 6 decimals
  // Ensure minimum 1 unit
  const finalAmount = amount > 0n ? amount : 1n;

  const txHash = cliArgs.txHash || ethers.ZeroHash;
  const expiry = Math.floor(Date.now() / 1000) + 3600;

  console.log(`\n  Gas voucher details:`);
  console.log(`    relayer : ${relayerAddr}`);
  console.log(`    amount  : ${finalAmount} (USDC units)`);
  console.log(`    nonce   : ${nonce}`);
  console.log(`    txHash  : ${txHash}`);

  // ── 6. Sign EIP-712 GasVoucher ──
  const domain = {
    name:              "GridGuardian-Collateral",
    version:           "1",
    chainId:           CHAIN_ID,
    verifyingContract: COLLATERAL_SC_ADDR,
  };

  const types = {
    GasVoucher: [
      { name: "nodeId",   type: "bytes32"  },
      { name: "relayer",  type: "address"  },
      { name: "amount",   type: "uint256"  },
      { name: "maxGas",   type: "uint256"  },
      { name: "gasPrice", type: "uint256"  },
      { name: "nonce",    type: "uint256"  },
      { name: "expiry",   type: "uint256"  },
      { name: "txHash",   type: "bytes32"  },
    ],
  };

  const message = {
    nodeId,
    relayer:  relayerAddr,
    amount:   finalAmount,
    maxGas:   gasUsed,
    gasPrice: gasPrice,
    nonce,
    expiry,
    txHash,
  };

  console.log("\nSigning EIP-712 GasVoucher...");
  const signature = await wallet.signTypedData(domain, types, message);
  console.log("  Signature:", signature);

  // ── 7. Submit to relayer ──
  const payload = {
    voucher: {
      nodeId,
      relayer: relayerAddr,
      amount:  finalAmount.toString(),
      maxGas:  gasUsed.toString(),
      gasPrice: gasPrice.toString(),
      nonce,
      expiry,
      txHash,
    },
    signature,
    signer: wallet.address,
  };

  console.log("\nSending voucher to relayer...");
  try {
    const resp = await axios.post(`${RELAYER_URL}/voucher/submit`, payload);
    console.log("✅  Relayer response:", resp.data);
  } catch (err) {
    console.error("❌  Voucher submission failed:", err.response?.data || err.message);
  }
}

main().catch((err) => {
  console.error("Voucher signing failed:", err);
  process.exit(1);
});
