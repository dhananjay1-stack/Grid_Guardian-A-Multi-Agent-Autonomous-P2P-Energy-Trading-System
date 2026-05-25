/**
 * tests/integration_settlement.test.js
 *
 * Integration test for Step 5: Settlement, Delivery & Audit Trail
 * Runs as a Hardhat test (uses built-in network).
 *
 * Scenarios:
 *   A. Happy path: propose → deliver → settle
 *   B. Timeout: propose → no delivery → refund
 *   C. Dispute: propose → deliver → dispute → resolve
 */
const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Step 5 Integration: Settlement Flow", function () {
  let mockUSDC, identitySC, collateralSC, settlementSC, deliveryRegistry;
  let deployer, buyer, seller, relayer;

  const USDC_DECIMALS = 6;
  const ONE_USDC = 10n ** BigInt(USDC_DECIMALS);
  const DEPOSIT_AMOUNT = 3000n * ONE_USDC;
  const TRADE_AMOUNT = 100n * ONE_USDC;
  const SETTLEMENT_TIMEOUT = 15;
  const DISPUTE_WINDOW = 10;
  const REQUIRED_RECEIPTS = 1;

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

  let buyerNodeId, sellerNodeId;

  function makeDeliveryDomain(contractAddr) {
    return {
      name: "GridGuardian-Delivery",
      version: "1",
      chainId: 31337,
      verifyingContract: contractAddr,
    };
  }

  async function signDeliveryReceipt(signer, contractAddr, receipt) {
    const domain = makeDeliveryDomain(contractAddr);
    return await signer.signTypedData(domain, DELIVERY_TYPES, receipt);
  }

  async function mineBlocks(count) {
    for (let i = 0; i < count; i++) {
      await ethers.provider.send("evm_mine", []);
    }
  }

  before(async function () {
    [deployer, buyer, seller, relayer] = await ethers.getSigners();

    // Deploy all contracts
    const MockUSDC = await ethers.getContractFactory("MockUSDC");
    mockUSDC = await MockUSDC.deploy(deployer.address);
    await mockUSDC.waitForDeployment();

    const IdentitySC = await ethers.getContractFactory("IdentitySC");
    identitySC = await IdentitySC.deploy(deployer.address);
    await identitySC.waitForDeployment();

    const CollateralSC = await ethers.getContractFactory("CollateralSC");
    collateralSC = await CollateralSC.deploy(
      await mockUSDC.getAddress(),
      await identitySC.getAddress(),
      deployer.address
    );
    await collateralSC.waitForDeployment();

    const SettlementSC = await ethers.getContractFactory("SettlementSC");
    settlementSC = await SettlementSC.deploy(
      await collateralSC.getAddress(),
      await identitySC.getAddress(),
      deployer.address,
      SETTLEMENT_TIMEOUT,
      DISPUTE_WINDOW
    );
    await settlementSC.waitForDeployment();

    const DeliveryRegistry = await ethers.getContractFactory("DeliveryRegistry");
    deliveryRegistry = await DeliveryRegistry.deploy(
      await identitySC.getAddress(),
      await settlementSC.getAddress(),
      deployer.address,
      REQUIRED_RECEIPTS
    );
    await deliveryRegistry.waitForDeployment();

    // Configure roles
    const SETTLEMENT_ROLE = await collateralSC.SETTLEMENT_ROLE();
    await collateralSC.grantRole(SETTLEMENT_ROLE, await settlementSC.getAddress());

    const MATCH_REGISTRY_ROLE = await settlementSC.MATCH_REGISTRY_ROLE();
    await settlementSC.grantRole(MATCH_REGISTRY_ROLE, deployer.address);

    const DELIVERY_REGISTRY_ROLE = await settlementSC.DELIVERY_REGISTRY_ROLE();
    await settlementSC.grantRole(DELIVERY_REGISTRY_ROLE, await deliveryRegistry.getAddress());

    // Register nodes
    buyerNodeId = ethers.keccak256(ethers.toUtf8Bytes("buyer-node-1"));
    sellerNodeId = ethers.keccak256(ethers.toUtf8Bytes("seller-node-1"));
    const bPubkeyHash = ethers.keccak256(ethers.toUtf8Bytes("buyer-pubkey"));
    const sPubkeyHash = ethers.keccak256(ethers.toUtf8Bytes("seller-pubkey"));

    await identitySC.connect(buyer).registerNode(buyerNodeId, bPubkeyHash, "ipfs://buyer");
    await identitySC.connect(seller).registerNode(sellerNodeId, sPubkeyHash, "ipfs://seller");

    // Mint and deposit USDC for buyer
    await mockUSDC.mint(buyer.address, DEPOSIT_AMOUNT);
    await mockUSDC.connect(buyer).approve(await collateralSC.getAddress(), DEPOSIT_AMOUNT);
    await collateralSC.connect(buyer).deposit(buyerNodeId, DEPOSIT_AMOUNT);
  });

  it("Scenario A: Happy path (propose → deliver → settle)", async function () {
    const matchHash = ethers.keccak256(ethers.toUtf8Bytes("match-A"));
    const tradeId = ethers.solidityPackedKeccak256(
      ["bytes32", "bytes32", "bytes32", "uint32"],
      [matchHash, buyerNodeId, sellerNodeId, 1]
    );

    // 1. Propose trade
    await settlementSC.proposeTrade(tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT);
    let trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(1); // Locked

    // 2. Submit delivery receipt
    const nonce = await deliveryRegistry.getDeliveryNonce(sellerNodeId);
    const meterHash = ethers.keccak256(ethers.toUtf8Bytes("meter-A"));
    const now = Math.floor(Date.now() / 1000);
    const receipt = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash: meterHash,
      deliveredKwhBucket: 10,
      periodStart: now,
      periodEnd: now + 3600,
      nonce,
    };
    const sig = await signDeliveryReceipt(seller, await deliveryRegistry.getAddress(), receipt);
    await deliveryRegistry.submitReceipt(tradeId, sellerNodeId, meterHash, 10, now, now + 3600, nonce, sig);

    trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(2); // Delivered

    // 3. Mine past dispute window
    await mineBlocks(DISPUTE_WINDOW + 1);

    // 4. Execute settlement
    const sellerBalBefore = await mockUSDC.balanceOf(seller.address);
    await settlementSC.executeSettlement(tradeId);
    const sellerBalAfter = await mockUSDC.balanceOf(seller.address);

    trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(3); // Settled
    expect(sellerBalAfter - sellerBalBefore).to.equal(TRADE_AMOUNT);
  });

  it("Scenario B: Timeout (propose → no delivery → refund)", async function () {
    const matchHash = ethers.keccak256(ethers.toUtf8Bytes("match-B"));
    const tradeId = ethers.solidityPackedKeccak256(
      ["bytes32", "bytes32", "bytes32", "uint32"],
      [matchHash, buyerNodeId, sellerNodeId, 2]
    );

    // 1. Propose trade
    const buyerDepositBefore = await collateralSC.getDeposit(buyerNodeId);
    await settlementSC.proposeTrade(tradeId, matchHash, buyerNodeId, sellerNodeId, 15, 45, TRADE_AMOUNT);
    let trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(1); // Locked

    // Verify funds locked
    const buyerDepositAfterLock = await collateralSC.getDeposit(buyerNodeId);
    expect(buyerDepositAfterLock).to.equal(buyerDepositBefore - TRADE_AMOUNT);

    // 2. Mine past settlement timeout (no delivery)
    await mineBlocks(SETTLEMENT_TIMEOUT + 1);

    // 3. Refund
    await settlementSC.refundTrade(tradeId);
    trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(5); // Refunded

    // Verify funds returned
    const buyerDepositAfterRefund = await collateralSC.getDeposit(buyerNodeId);
    expect(buyerDepositAfterRefund).to.equal(buyerDepositBefore);
  });

  it("Scenario C: Dispute (propose → deliver → dispute → resolve)", async function () {
    const matchHash = ethers.keccak256(ethers.toUtf8Bytes("match-C"));
    const tradeId = ethers.solidityPackedKeccak256(
      ["bytes32", "bytes32", "bytes32", "uint32"],
      [matchHash, buyerNodeId, sellerNodeId, 3]
    );

    // 1. Propose trade
    await settlementSC.proposeTrade(tradeId, matchHash, buyerNodeId, sellerNodeId, 20, 55, TRADE_AMOUNT);
    let trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(1); // Locked

    // 2. Submit delivery receipt
    const nonce = await deliveryRegistry.getDeliveryNonce(sellerNodeId);
    const meterHash = ethers.keccak256(ethers.toUtf8Bytes("meter-C"));
    const now = Math.floor(Date.now() / 1000);
    const receipt = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash: meterHash,
      deliveredKwhBucket: 20,
      periodStart: now,
      periodEnd: now + 3600,
      nonce,
    };
    const sig = await signDeliveryReceipt(seller, await deliveryRegistry.getAddress(), receipt);
    await deliveryRegistry.submitReceipt(tradeId, sellerNodeId, meterHash, 20, now, now + 3600, nonce, sig);

    trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(2); // Delivered

    // 3. Buyer disputes (within window)
    await settlementSC.connect(buyer).disputeTrade(tradeId);
    trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(4); // Disputed

    // 4. Adjudicator resolves (seller wins, sellerFavored=true)
    const sellerBalBefore = await mockUSDC.balanceOf(seller.address);
    await settlementSC.resolveDispute(tradeId, true, TRADE_AMOUNT, 0);
    const sellerBalAfter = await mockUSDC.balanceOf(seller.address);

    trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(3); // Settled
    expect(sellerBalAfter - sellerBalBefore).to.equal(TRADE_AMOUNT);
  });
});
