/**
 * scripts/integration/settlement_flow.js
 *
 * End-to-end integration test for Step 5: Settlement, Delivery & Audit Trail
 *
 * Scenarios:
 *   A. Happy path: propose → deliver → settle
 *   B. Timeout: propose → no delivery → refund
 *   C. Dispute: propose → deliver → dispute → resolve
 *
 * Prerequisites:
 *   - Hardhat node running on :8545
 *   - Contracts compiled (`npx hardhat compile`)
 *
 * Usage:
 *   node scripts/integration/settlement_flow.js
 */
require("dotenv").config();
const { ethers } = require("ethers");
const path = require("path");
const fs = require("fs");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL = process.env.RPC_URL || "http://127.0.0.1:8545";
const USDC_DECIMALS = 6;
const ONE_USDC = 10n ** BigInt(USDC_DECIMALS);
const DEPOSIT_AMOUNT = 1000n * ONE_USDC;
const TRADE_AMOUNT = 100n * ONE_USDC;
const SETTLEMENT_TIMEOUT = 15; // short for integration test
const DISPUTE_WINDOW = 10;     // short for integration test
const REQUIRED_RECEIPTS = 1;

// ── Load artifact ──────────────────────────────────────────────────
function loadArtifact(name) {
  const p = path.resolve(__dirname, `../../artifacts/contracts/${name}.sol/${name}.json`);
  if (!fs.existsSync(p)) {
    console.error(`Artifact not found: ${name}. Run \`npx hardhat compile\` first.`);
    process.exit(1);
  }
  const a = JSON.parse(fs.readFileSync(p, "utf-8"));
  return { abi: a.abi, bytecode: a.bytecode };
}

// ── EIP-712 Delivery Receipt ───────────────────────────────────────
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

function getDeliveryDomain(verifyingContract) {
  return {
    name: "GridGuardian-Delivery",
    version: "1",
    chainId: 31337,
    verifyingContract,
  };
}

async function signDeliveryReceipt(wallet, contractAddr, receipt) {
  const domain = getDeliveryDomain(contractAddr);
  return await wallet.signTypedData(domain, DELIVERY_TYPES, receipt);
}

// ── Mine blocks helper ─────────────────────────────────────────────
async function mineBlocks(provider, count) {
  for (let i = 0; i < count; i++) {
    await provider.send("evm_mine", []);
  }
}

// ── Main ───────────────────────────────────────────────────────────
async function main() {
  console.log("======================================================================");
  console.log("   Step 5: Settlement & Delivery Integration Test");
  console.log("======================================================================\n");

  const provider = new ethers.JsonRpcProvider(RPC_URL);

  // Signers
  const deployer = await provider.getSigner(0);
  const buyer = await provider.getSigner(1);
  const seller = await provider.getSigner(2);
  const relayer = await provider.getSigner(3);

  const deployerAddr = await deployer.getAddress();
  const buyerAddr = await buyer.getAddress();
  const sellerAddr = await seller.getAddress();
  const relayerAddr = await relayer.getAddress();

  console.log(`  Deployer : ${deployerAddr}`);
  console.log(`  Buyer    : ${buyerAddr}`);
  console.log(`  Seller   : ${sellerAddr}`);
  console.log(`  Relayer  : ${relayerAddr}\n`);

  // ═══════════════════════════════════════════════════════════════════
  //                     Deploy All Contracts
  // ═══════════════════════════════════════════════════════════════════
  console.log("1. Deploying contracts...\n");

  // MockUSDC
  const usdcArt = loadArtifact("MockUSDC");
  const USDCFactory = new ethers.ContractFactory(usdcArt.abi, usdcArt.bytecode, deployer);
  const mockUSDC = await USDCFactory.deploy(deployerAddr);
  await mockUSDC.waitForDeployment();
  const usdcAddr = await mockUSDC.getAddress();
  console.log(`   MockUSDC: ${usdcAddr}`);

  // IdentitySC
  const idArt = loadArtifact("IdentitySC");
  const IDFactory = new ethers.ContractFactory(idArt.abi, idArt.bytecode, deployer);
  const identitySC = await IDFactory.deploy(deployerAddr);
  await identitySC.waitForDeployment();
  const idAddr = await identitySC.getAddress();
  console.log(`   IdentitySC: ${idAddr}`);

  // CollateralSC
  const colArt = loadArtifact("CollateralSC");
  const ColFactory = new ethers.ContractFactory(colArt.abi, colArt.bytecode, deployer);
  const collateralSC = await ColFactory.deploy(usdcAddr, idAddr, deployerAddr);
  await collateralSC.waitForDeployment();
  const colAddr = await collateralSC.getAddress();
  console.log(`   CollateralSC: ${colAddr}`);

  // SettlementSC
  const setArt = loadArtifact("SettlementSC");
  const SetFactory = new ethers.ContractFactory(setArt.abi, setArt.bytecode, deployer);
  const settlementSC = await SetFactory.deploy(
    colAddr, idAddr, deployerAddr, SETTLEMENT_TIMEOUT, DISPUTE_WINDOW
  );
  await settlementSC.waitForDeployment();
  const setAddr = await settlementSC.getAddress();
  console.log(`   SettlementSC: ${setAddr}`);

  // DeliveryRegistry
  const delArt = loadArtifact("DeliveryRegistry");
  const DelFactory = new ethers.ContractFactory(delArt.abi, delArt.bytecode, deployer);
  const deliveryRegistry = await DelFactory.deploy(idAddr, setAddr, deployerAddr, REQUIRED_RECEIPTS);
  await deliveryRegistry.waitForDeployment();
  const delAddr = await deliveryRegistry.getAddress();
  console.log(`   DeliveryRegistry: ${delAddr}`);

  // ═══════════════════════════════════════════════════════════════════
  //                     Configure Roles
  // ═══════════════════════════════════════════════════════════════════
  console.log("\n2. Configuring roles...");

  // Grant SETTLEMENT_ROLE to SettlementSC on CollateralSC
  const SETTLEMENT_ROLE = await collateralSC.SETTLEMENT_ROLE();
  await (await collateralSC.grantRole(SETTLEMENT_ROLE, setAddr)).wait();
  console.log("   SETTLEMENT_ROLE on CollateralSC -> SettlementSC");

  // Grant MATCH_REGISTRY_ROLE to deployer for testing
  const MATCH_REGISTRY_ROLE = await settlementSC.MATCH_REGISTRY_ROLE();
  await (await settlementSC.grantRole(MATCH_REGISTRY_ROLE, deployerAddr)).wait();
  console.log("   MATCH_REGISTRY_ROLE on SettlementSC -> Deployer");

  // Grant DELIVERY_REGISTRY_ROLE to DeliveryRegistry on SettlementSC
  const DELIVERY_REGISTRY_ROLE = await settlementSC.DELIVERY_REGISTRY_ROLE();
  await (await settlementSC.grantRole(DELIVERY_REGISTRY_ROLE, delAddr)).wait();
  console.log("   DELIVERY_REGISTRY_ROLE on SettlementSC -> DeliveryRegistry");

  // ═══════════════════════════════════════════════════════════════════
  //                     Register Nodes & Deposit
  // ═══════════════════════════════════════════════════════════════════
  console.log("\n3. Registering nodes and depositing collateral...");

  const buyerNodeId = ethers.keccak256(ethers.toUtf8Bytes("buyer-node-1"));
  const sellerNodeId = ethers.keccak256(ethers.toUtf8Bytes("seller-node-1"));
  const bPubkeyHash = ethers.keccak256(ethers.toUtf8Bytes("buyer-pubkey"));
  const sPubkeyHash = ethers.keccak256(ethers.toUtf8Bytes("seller-pubkey"));

  await (await identitySC.connect(buyer).registerNode(buyerNodeId, bPubkeyHash, "ipfs://buyer")).wait();
  await (await identitySC.connect(seller).registerNode(sellerNodeId, sPubkeyHash, "ipfs://seller")).wait();
  console.log("   Buyer and Seller nodes registered");

  // Mint USDC to buyer and deposit
  await (await mockUSDC.mint(buyerAddr, DEPOSIT_AMOUNT * 3n)).wait();
  await (await mockUSDC.connect(buyer).approve(colAddr, DEPOSIT_AMOUNT * 3n)).wait();
  await (await collateralSC.connect(buyer).deposit(buyerNodeId, DEPOSIT_AMOUNT * 3n)).wait();
  console.log(`   Buyer deposited ${DEPOSIT_AMOUNT * 3n} USDC`);

  // ═══════════════════════════════════════════════════════════════════
  //   SCENARIO A: Happy Path (propose -> deliver -> settle)
  // ═══════════════════════════════════════════════════════════════════
  console.log("\n======================================================================");
  console.log("  Scenario A: Happy Path (propose -> deliver -> settle)");
  console.log("======================================================================\n");

  const matchHashA = ethers.keccak256(ethers.toUtf8Bytes("match-A"));
  const tradeIdA = ethers.solidityPackedKeccak256(
    ["bytes32", "bytes32", "bytes32", "uint32"],
    [matchHashA, buyerNodeId, sellerNodeId, 1]
  );

  console.log("4. Proposing trade A...");
  await (await settlementSC.proposeTrade(tradeIdA, matchHashA, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT)).wait();
  let tradeA = await settlementSC.getTrade(tradeIdA);
  console.log(`   Trade status: ${tradeA.status} (expected 1 = Locked)`);
  if (Number(tradeA.status) !== 1) throw new Error("Trade A not Locked!");

  console.log("\n5. Submitting delivery receipt A...");
  const nonceA = await deliveryRegistry.getDeliveryNonce(sellerNodeId);
  const meterHashA = ethers.keccak256(ethers.toUtf8Bytes("meter-A"));
  const now = Math.floor(Date.now() / 1000);
  const receiptA = {
    tradeId: tradeIdA,
    nodeId: sellerNodeId,
    meterSnapshotHash: meterHashA,
    deliveredKwhBucket: 10,
    periodStart: now,
    periodEnd: now + 3600,
    nonce: nonceA,
  };
  const sigA = await signDeliveryReceipt(seller, delAddr, receiptA);
  await (await deliveryRegistry.submitReceipt(
    tradeIdA, sellerNodeId, meterHashA, 10, now, now + 3600, nonceA, sigA
  )).wait();

  tradeA = await settlementSC.getTrade(tradeIdA);
  console.log(`   Trade status: ${tradeA.status} (expected 2 = Delivered)`);
  if (Number(tradeA.status) !== 2) throw new Error("Trade A not Delivered!");

  console.log("\n6. Mining past dispute window...");
  await mineBlocks(provider, DISPUTE_WINDOW + 1);
  console.log(`   ${DISPUTE_WINDOW + 1} blocks mined`);

  console.log("\n7. Executing settlement A...");
  const sellerBalBefore = await mockUSDC.balanceOf(sellerAddr);
  await (await settlementSC.executeSettlement(tradeIdA)).wait();
  const sellerBalAfter = await mockUSDC.balanceOf(sellerAddr);

  tradeA = await settlementSC.getTrade(tradeIdA);
  console.log(`   Trade status: ${tradeA.status} (expected 3 = Settled)`);
  console.log(`   Seller balance: ${sellerBalBefore} -> ${sellerBalAfter} (+${sellerBalAfter - sellerBalBefore})`);

  if (Number(tradeA.status) !== 3) throw new Error("Trade A not Settled!");
  if (sellerBalAfter - sellerBalBefore !== TRADE_AMOUNT) throw new Error("Seller did not receive funds!");
  console.log("   Scenario A PASSED!\n");

  // ═══════════════════════════════════════════════════════════════════
  //   SCENARIO B: Timeout (propose -> no delivery -> refund)
  // ═══════════════════════════════════════════════════════════════════
  console.log("======================================================================");
  console.log("  Scenario B: Timeout (propose -> no delivery -> refund)");
  console.log("======================================================================\n");

  const matchHashB = ethers.keccak256(ethers.toUtf8Bytes("match-B"));
  const tradeIdB = ethers.solidityPackedKeccak256(
    ["bytes32", "bytes32", "bytes32", "uint32"],
    [matchHashB, buyerNodeId, sellerNodeId, 2]
  );

  console.log("8. Proposing trade B...");
  const buyerDepositBefore = await collateralSC.getDeposit(buyerNodeId);
  await (await settlementSC.proposeTrade(tradeIdB, matchHashB, buyerNodeId, sellerNodeId, 15, 45, TRADE_AMOUNT)).wait();
  let tradeB = await settlementSC.getTrade(tradeIdB);
  console.log(`   Trade status: ${tradeB.status} (expected 1 = Locked)`);
  console.log(`   Buyer deposit: ${buyerDepositBefore} -> ${await collateralSC.getDeposit(buyerNodeId)}`);

  console.log("\n9. Mining past settlement timeout (no delivery)...");
  await mineBlocks(provider, SETTLEMENT_TIMEOUT + 1);
  console.log(`   ${SETTLEMENT_TIMEOUT + 1} blocks mined`);

  console.log("\n10. Refunding trade B...");
  await (await settlementSC.refundTrade(tradeIdB)).wait();
  tradeB = await settlementSC.getTrade(tradeIdB);
  const buyerDepositAfter = await collateralSC.getDeposit(buyerNodeId);

  console.log(`   Trade status: ${tradeB.status} (expected 5 = Refunded)`);
  console.log(`   Buyer deposit restored: ${buyerDepositAfter}`);

  if (Number(tradeB.status) !== 5) throw new Error("Trade B not Refunded!");
  console.log("   Scenario B PASSED!\n");

  // ═══════════════════════════════════════════════════════════════════
  //   SCENARIO C: Dispute (propose -> deliver -> dispute -> resolve)
  // ═══════════════════════════════════════════════════════════════════
  console.log("======================================================================");
  console.log("  Scenario C: Dispute (propose -> deliver -> dispute -> resolve)");
  console.log("======================================================================\n");

  const matchHashC = ethers.keccak256(ethers.toUtf8Bytes("match-C"));
  const tradeIdC = ethers.solidityPackedKeccak256(
    ["bytes32", "bytes32", "bytes32", "uint32"],
    [matchHashC, buyerNodeId, sellerNodeId, 3]
  );

  console.log("11. Proposing trade C...");
  await (await settlementSC.proposeTrade(tradeIdC, matchHashC, buyerNodeId, sellerNodeId, 20, 55, TRADE_AMOUNT)).wait();
  let tradeC = await settlementSC.getTrade(tradeIdC);
  console.log(`   Trade status: ${tradeC.status} (expected 1 = Locked)`);

  console.log("\n12. Submitting delivery receipt C...");
  const nonceC = await deliveryRegistry.getDeliveryNonce(sellerNodeId);
  const meterHashC = ethers.keccak256(ethers.toUtf8Bytes("meter-C"));
  const nowC = Math.floor(Date.now() / 1000);
  const receiptC = {
    tradeId: tradeIdC,
    nodeId: sellerNodeId,
    meterSnapshotHash: meterHashC,
    deliveredKwhBucket: 20,
    periodStart: nowC,
    periodEnd: nowC + 3600,
    nonce: nonceC,
  };
  const sigC = await signDeliveryReceipt(seller, delAddr, receiptC);
  await (await deliveryRegistry.submitReceipt(
    tradeIdC, sellerNodeId, meterHashC, 20, nowC, nowC + 3600, nonceC, sigC
  )).wait();

  tradeC = await settlementSC.getTrade(tradeIdC);
  console.log(`   Trade status: ${tradeC.status} (expected 2 = Delivered)`);

  console.log("\n13. Buyer disputes trade C...");
  await (await settlementSC.connect(buyer).disputeTrade(tradeIdC)).wait();
  tradeC = await settlementSC.getTrade(tradeIdC);
  console.log(`   Trade status: ${tradeC.status} (expected 4 = Disputed)`);
  if (Number(tradeC.status) !== 4) throw new Error("Trade C not Disputed!");

  console.log("\n14. Adjudicator resolves dispute (seller wins)...");
  const seller2BalBefore = await mockUSDC.balanceOf(sellerAddr);
  await (await settlementSC.resolveDispute(tradeIdC, false, TRADE_AMOUNT, 0)).wait();
  const seller2BalAfter = await mockUSDC.balanceOf(sellerAddr);

  tradeC = await settlementSC.getTrade(tradeIdC);
  console.log(`   Trade status: ${tradeC.status} (expected 3 = Settled)`);
  console.log(`   Seller balance: ${seller2BalBefore} -> ${seller2BalAfter} (+${seller2BalAfter - seller2BalBefore})`);

  if (Number(tradeC.status) !== 3) throw new Error("Trade C not Settled after dispute!");
  console.log("   Scenario C PASSED!\n");

  // ═══════════════════════════════════════════════════════════════════
  //                           Summary
  // ═══════════════════════════════════════════════════════════════════
  console.log("======================================================================");
  console.log("   All Step 5 integration checks PASSED!");
  console.log("   * Scenario A: Happy path (propose -> deliver -> settle)");
  console.log("   * Scenario B: Timeout (propose -> no delivery -> refund)");
  console.log("   * Scenario C: Dispute (propose -> deliver -> dispute -> resolve)");
  console.log("======================================================================");
}

main().catch((err) => {
  console.error("\nIntegration test FAILED:", err.message || err);
  process.exit(1);
});
