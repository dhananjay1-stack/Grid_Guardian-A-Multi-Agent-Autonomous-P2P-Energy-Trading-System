/**
 * scripts/integration/gasFlow.js
 *
 * End-to-end integration test for Step 2: Collateral & Gas Setup
 *
 *   1. Connect to running Hardhat node.
 *   2. Deploy IdentitySC, MockUSDC, CollateralSC.
 *   3. Register a device node in IdentitySC.
 *   4. Mint MockUSDC to device, deposit into CollateralSC.
 *   5. Device approves relayer allowance.
 *   6. Relayer executes a meta-tx (registerNodeMeta for a second device).
 *   7. Device signs GasVoucher for the executed tx.
 *   8. Relayer claims gas reimbursement via claimGas.
 *   9. Verify balances, allowances, nonces.
 *
 * Prerequisites:
 *   - Hardhat node running on :8545
 *
 * Usage:
 *   node scripts/integration/gasFlow.js
 */
require("dotenv").config();
const { ethers } = require("ethers");
const crypto = require("crypto");
const path   = require("path");
const fs     = require("fs");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL = process.env.RPC_URL || "http://127.0.0.1:8545";

// ── Load contract artifacts ────────────────────────────────────────
function loadArtifact(name) {
  const p = path.resolve(__dirname, `../../artifacts/contracts/${name}.sol/${name}.json`);
  if (!fs.existsSync(p)) {
    console.error(`❌  Artifact not found: ${name}. Run \`npx hardhat compile\` first.`);
    process.exit(1);
  }
  const a = JSON.parse(fs.readFileSync(p, "utf-8"));
  return { abi: a.abi, bytecode: a.bytecode };
}

const { abi: IDENTITY_ABI, bytecode: IDENTITY_BC }   = loadArtifact("IdentitySC");
const { abi: USDC_ABI,     bytecode: USDC_BC }        = loadArtifact("MockUSDC");
const { abi: COLLATERAL_ABI, bytecode: COLLATERAL_BC } = loadArtifact("CollateralSC");

// ── EIP-712 types ──────────────────────────────────────────────────
const VOUCHER_TYPES = {
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

const REGISTER_TYPES = {
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
  console.log("  Grid-Guardian — Integration Test: Gas Flow (Step 2)");
  console.log("═══════════════════════════════════════════════════════\n");

  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const deployer = await provider.getSigner(0);  // Hardhat account #0
  const relayer  = await provider.getSigner(1);   // Hardhat account #1

  const deployerAddr = await deployer.getAddress();
  const relayerAddr  = await relayer.getAddress();

  // ── 1. Deploy IdentitySC ──
  console.log("1️⃣  Deploying IdentitySC...");
  const identityFactory = new ethers.ContractFactory(IDENTITY_ABI, IDENTITY_BC, deployer);
  const identity = await identityFactory.deploy(deployerAddr);
  await identity.waitForDeployment();
  const identityAddr = await identity.getAddress();
  console.log(`   ✅  IdentitySC at: ${identityAddr}`);

  // Grant RELAYER_ROLE + ADMIN_ROLE to relayer
  const RELAYER_ROLE = ethers.keccak256(ethers.toUtf8Bytes("RELAYER_ROLE"));
  const ADMIN_ROLE   = ethers.keccak256(ethers.toUtf8Bytes("ADMIN_ROLE"));
  await (await identity.grantRole(RELAYER_ROLE, relayerAddr)).wait();
  await (await identity.grantRole(ADMIN_ROLE, relayerAddr)).wait();

  // ── 2. Deploy MockUSDC ──
  console.log("2️⃣  Deploying MockUSDC...");
  const usdcFactory = new ethers.ContractFactory(USDC_ABI, USDC_BC, deployer);
  const usdc = await usdcFactory.deploy(deployerAddr);
  await usdc.waitForDeployment();
  const usdcAddr = await usdc.getAddress();
  console.log(`   ✅  MockUSDC at: ${usdcAddr}`);

  // ── 3. Deploy CollateralSC ──
  console.log("3️⃣  Deploying CollateralSC...");
  const collateralFactory = new ethers.ContractFactory(COLLATERAL_ABI, COLLATERAL_BC, deployer);
  const collateral = await collateralFactory.deploy(usdcAddr, identityAddr, deployerAddr);
  await collateral.waitForDeployment();
  const collateralAddr = await collateral.getAddress();
  console.log(`   ✅  CollateralSC at: ${collateralAddr}\n`);

  // ── 4. Register device1 in IdentitySC ──
  console.log("4️⃣  Registering device node...");
  const deviceWallet = ethers.Wallet.createRandom().connect(provider);
  const salt = crypto.randomBytes(32);
  const pubkeyBytes = ethers.getBytes(deviceWallet.signingKey.compressedPublicKey);
  const nodeId = ethers.keccak256(ethers.concat([pubkeyBytes, salt]));
  const pubkeyHash = ethers.keccak256(pubkeyBytes);

  // Register via meta-tx (relayer pays gas)
  const regDomain = {
    name: "GridGuardian-Relayer", version: "1",
    chainId: 31337, verifyingContract: identityAddr,
  };
  const regMsg = { nodeId, pubkeyHash, metaURI: "ipfs://device1-profile", nonce: 0, expiry: Math.floor(Date.now() / 1000) + 3600 };
  const regSig = await deviceWallet.signTypedData(regDomain, REGISTER_TYPES, regMsg);

  const relayerIdentity = new ethers.Contract(identityAddr, IDENTITY_ABI, relayer);
  await (await relayerIdentity.registerNodeMeta(nodeId, pubkeyHash, regMsg.metaURI, regMsg.nonce, regMsg.expiry, regSig)).wait();
  console.log(`   ✅  Device registered: nodeId=${nodeId.slice(0,18)}...`);
  console.log(`       owner: ${deviceWallet.address}\n`);

  // ── 5. Mint USDC to device & deposit into CollateralSC ──
  console.log("5️⃣  Minting USDC & depositing collateral...");
  const DEPOSIT = 1000n * 10n ** 6n; // 1000 USDC

  // Deployer (usdc owner) mints to device's address, then device deposits
  // But device has no ETH for gas — deployer deposits on behalf of device
  await (await usdc.mint(deployerAddr, DEPOSIT)).wait();
  await (await usdc.approve(collateralAddr, DEPOSIT)).wait();

  // CollateralSC.deposit can be called by anyone (they transfer tokens)
  const deployerCollateral = new ethers.Contract(collateralAddr, COLLATERAL_ABI, deployer);
  await (await deployerCollateral.deposit(nodeId, DEPOSIT)).wait();
  const depositBal = await collateral.deposits(nodeId);
  assert(depositBal === DEPOSIT, `deposit mismatch: ${depositBal} != ${DEPOSIT}`);
  console.log(`   ✅  Deposited ${DEPOSIT / 10n ** 6n} USDC for node\n`);

  // ── 6. Device approves relayer allowance ──
  // Device has no ETH — so deployer must fund device first (or use meta-tx)
  // For integration test, fund device with a tiny amount of ETH
  console.log("6️⃣  Approving relayer allowance...");
  await (await deployer.sendTransaction({ to: deviceWallet.address, value: ethers.parseEther("0.1") })).wait();

  const deviceCollateral = new ethers.Contract(collateralAddr, COLLATERAL_ABI, deviceWallet);
  const ALLOWANCE = 100n * 10n ** 6n; // 100 USDC
  await (await deviceCollateral.approveRelayer(nodeId, relayerAddr, ALLOWANCE)).wait();
  const storedAllowance = await collateral.relayerAllowance(nodeId, relayerAddr);
  assert(storedAllowance === ALLOWANCE, "allowance mismatch");
  console.log(`   ✅  Relayer allowance: ${ALLOWANCE / 10n ** 6n} USDC\n`);

  // ── 7. Relayer executes a meta-tx (attest node) ──
  console.log("7️⃣  Relayer executing attestNode (meta-tx)...");
  const attestTx = await relayerIdentity.attestNode(nodeId);
  const attestReceipt = await attestTx.wait();
  console.log(`   ✅  attestNode tx: ${attestReceipt.hash}`);
  console.log(`       gasUsed: ${attestReceipt.gasUsed}\n`);

  // ── 8. Device signs GasVoucher ──
  console.log("8️⃣  Device signing GasVoucher...");
  const voucherNonce = Number(await collateral.voucherNonce(nodeId));
  const claimAmount = 5n * 10n ** 6n; // 5 USDC

  const voucherDomain = {
    name: "GridGuardian-Collateral", version: "1",
    chainId: 31337, verifyingContract: collateralAddr,
  };
  const voucher = {
    nodeId,
    relayer:  relayerAddr,
    amount:   claimAmount,
    maxGas:   attestReceipt.gasUsed,
    gasPrice: attestReceipt.gasPrice,
    nonce:    voucherNonce,
    expiry:   Math.floor(Date.now() / 1000) + 3600,
    txHash:   attestReceipt.hash,
  };
  const voucherSig = await deviceWallet.signTypedData(voucherDomain, VOUCHER_TYPES, voucher);
  console.log(`   ✅  Voucher signed (amount: ${claimAmount / 10n ** 6n} USDC)\n`);

  // ── 9. Relayer claims gas reimbursement ──
  console.log("9️⃣  Relayer claiming gas reimbursement...");
  const relayerBalBefore = await usdc.balanceOf(relayerAddr);
  const relayerCollateral = new ethers.Contract(collateralAddr, COLLATERAL_ABI, relayer);
  const claimTx = await relayerCollateral.claimGas(voucher, voucherSig);
  const claimReceipt = await claimTx.wait();

  const relayerBalAfter = await usdc.balanceOf(relayerAddr);
  assert(relayerBalAfter - relayerBalBefore === claimAmount, "relayer balance mismatch");
  console.log(`   ✅  Claimed ${claimAmount / 10n ** 6n} USDC — tx: ${claimReceipt.hash}\n`);

  // ── 10. Verify final state ──
  console.log("🔍  Verifying final state...");
  const finalDeposit = await collateral.deposits(nodeId);
  assert(finalDeposit === DEPOSIT - claimAmount, "deposit not decremented");
  console.log(`   ✅  Deposit: ${finalDeposit / 10n ** 6n} USDC (was ${DEPOSIT / 10n ** 6n})`);

  const finalAllowance = await collateral.relayerAllowance(nodeId, relayerAddr);
  assert(finalAllowance === ALLOWANCE - claimAmount, "allowance not decremented");
  console.log(`   ✅  Allowance: ${finalAllowance / 10n ** 6n} USDC (was ${ALLOWANCE / 10n ** 6n})`);

  const finalNonce = Number(await collateral.voucherNonce(nodeId));
  assert(finalNonce === 1, "nonce not incremented");
  console.log(`   ✅  Voucher nonce: ${finalNonce}`);

  // Verify replay protection
  console.log("\n🔒  Testing replay protection...");
  try {
    await relayerCollateral.claimGas(voucher, voucherSig);
    assert(false, "replay should have reverted");
  } catch (err) {
    assert(err.message.includes("InvalidVoucherNonce") || err.message.includes("revert"), "unexpected error");
    console.log("   ✅  Replay correctly rejected");
  }

  console.log("\n═══════════════════════════════════════════════════════");
  console.log("  ✅  ALL GAS FLOW INTEGRATION CHECKS PASSED");
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
