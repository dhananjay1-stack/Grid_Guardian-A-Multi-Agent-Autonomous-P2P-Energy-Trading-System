/**
 * tests/delivery.test.js — DeliveryRegistry unit tests (Hardhat + Chai)
 *
 * Covers:
 *   1.  submitReceipt success - stores receipt, emits DeliveryReceiptSubmitted
 *   2.  submitReceipt verifies EIP-712 signature correctly
 *   3.  submitReceipt rejects invalid signature (wrong signer)
 *   4.  submitReceipt rejects replay (wrong nonce)
 *   5.  submitReceipt rejects duplicate receipt from same node
 *   6.  sufficient receipts triggers DeliveryConfirmed + calls markDelivered
 *   7.  getReceipt view helper returns correct data
 */
const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("DeliveryRegistry", function () {
  let mockUSDC, identitySC, collateralSC, settlementSC, deliveryRegistry;
  let admin, buyer, seller, relayer, outsider;

  const USDC_DECIMALS = 6;
  const ONE_USDC = 10n ** BigInt(USDC_DECIMALS);
  const DEPOSIT_AMOUNT = 1000n * ONE_USDC;
  const TRADE_AMOUNT = 100n * ONE_USDC;
  const SETTLEMENT_TIMEOUT = 120;
  const DISPUTE_WINDOW = 60;
  const REQUIRED_RECEIPTS = 1; // single-party for pilot

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
  let matchHash, tradeId;

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

  beforeEach(async function () {
    [admin, buyer, seller, relayer, outsider] = await ethers.getSigners();

    // Deploy MockUSDC
    const MockUSDC = await ethers.getContractFactory("MockUSDC");
    mockUSDC = await MockUSDC.deploy(admin.address);
    await mockUSDC.waitForDeployment();

    // Deploy IdentitySC
    const IdentitySC = await ethers.getContractFactory("IdentitySC");
    identitySC = await IdentitySC.deploy(admin.address);
    await identitySC.waitForDeployment();

    // Deploy CollateralSC
    const CollateralSC = await ethers.getContractFactory("CollateralSC");
    collateralSC = await CollateralSC.deploy(
      await mockUSDC.getAddress(),
      await identitySC.getAddress(),
      admin.address
    );
    await collateralSC.waitForDeployment();

    // Deploy SettlementSC
    const SettlementSC = await ethers.getContractFactory("SettlementSC");
    settlementSC = await SettlementSC.deploy(
      await collateralSC.getAddress(),
      await identitySC.getAddress(),
      admin.address,
      SETTLEMENT_TIMEOUT,
      DISPUTE_WINDOW
    );
    await settlementSC.waitForDeployment();

    // Deploy DeliveryRegistry
    const DeliveryRegistry = await ethers.getContractFactory("DeliveryRegistry");
    deliveryRegistry = await DeliveryRegistry.deploy(
      await identitySC.getAddress(),
      await settlementSC.getAddress(),
      admin.address,
      REQUIRED_RECEIPTS
    );
    await deliveryRegistry.waitForDeployment();

    // Grant SETTLEMENT_ROLE to SettlementSC on CollateralSC
    const SETTLEMENT_ROLE = await collateralSC.SETTLEMENT_ROLE();
    await collateralSC.connect(admin).grantRole(SETTLEMENT_ROLE, await settlementSC.getAddress());

    // Grant MATCH_REGISTRY_ROLE to admin for testing
    const MATCH_REGISTRY_ROLE = await settlementSC.MATCH_REGISTRY_ROLE();
    await settlementSC.connect(admin).grantRole(MATCH_REGISTRY_ROLE, admin.address);

    // Grant DELIVERY_REGISTRY_ROLE to DeliveryRegistry on SettlementSC
    const DELIVERY_REGISTRY_ROLE = await settlementSC.DELIVERY_REGISTRY_ROLE();
    await settlementSC.connect(admin).grantRole(DELIVERY_REGISTRY_ROLE, await deliveryRegistry.getAddress());

    // Register buyer and seller nodes
    buyerNodeId = ethers.keccak256(ethers.toUtf8Bytes("buyer-node-1"));
    sellerNodeId = ethers.keccak256(ethers.toUtf8Bytes("seller-node-1"));
    const bPubkeyHash = ethers.keccak256(ethers.toUtf8Bytes("buyer-pubkey"));
    const sPubkeyHash = ethers.keccak256(ethers.toUtf8Bytes("seller-pubkey"));

    await identitySC.connect(buyer).registerNode(buyerNodeId, bPubkeyHash, "ipfs://buyer");
    await identitySC.connect(seller).registerNode(sellerNodeId, sPubkeyHash, "ipfs://seller");

    // Mint USDC to buyer and deposit to collateral
    await mockUSDC.mint(buyer.address, DEPOSIT_AMOUNT);
    await mockUSDC.connect(buyer).approve(await collateralSC.getAddress(), DEPOSIT_AMOUNT);
    await collateralSC.connect(buyer).deposit(buyerNodeId, DEPOSIT_AMOUNT);

    // Create a trade
    matchHash = ethers.keccak256(ethers.toUtf8Bytes("match-1"));
    tradeId = ethers.solidityPackedKeccak256(
      ["bytes32", "bytes32", "bytes32", "uint32"],
      [matchHash, buyerNodeId, sellerNodeId, 1]
    );

    // Propose trade so it's in Locked state
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );
  });

  // ─────────────────────────────────────────────────────────────────
  //                     submitReceipt tests (1-5)
  // ─────────────────────────────────────────────────────────────────

  it("1. submitReceipt success - stores receipt, emits DeliveryReceiptSubmitted", async function () {
    const meterSnapshotHash = ethers.keccak256(ethers.toUtf8Bytes("meter-reading-123"));
    const deliveredKwhBucket = 10;
    const periodStart = Math.floor(Date.now() / 1000);
    const periodEnd = periodStart + 3600;
    const nonce = await deliveryRegistry.getDeliveryNonce(sellerNodeId);

    const receipt = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce,
    };

    const signature = await signDeliveryReceipt(
      seller,
      await deliveryRegistry.getAddress(),
      receipt
    );

    await expect(
      deliveryRegistry.submitReceipt(
        tradeId,
        sellerNodeId,
        meterSnapshotHash,
        deliveredKwhBucket,
        periodStart,
        periodEnd,
        nonce,
        signature
      )
    )
      .to.emit(deliveryRegistry, "DeliveryReceiptSubmitted")
      .withArgs(tradeId, sellerNodeId, meterSnapshotHash, deliveredKwhBucket, await ethers.provider.getBlockNumber() + 1);
  });

  it("2. submitReceipt verifies EIP-712 signature correctly", async function () {
    const meterSnapshotHash = ethers.keccak256(ethers.toUtf8Bytes("meter-reading-456"));
    const deliveredKwhBucket = 15;
    const periodStart = Math.floor(Date.now() / 1000);
    const periodEnd = periodStart + 3600;
    const nonce = 0n;

    const receipt = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce,
    };

    const signature = await signDeliveryReceipt(
      seller,
      await deliveryRegistry.getAddress(),
      receipt
    );

    // Should succeed with correct signature
    await deliveryRegistry.submitReceipt(
      tradeId,
      sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce,
      signature
    );

    // Verify receipt stored
    const storedReceipt = await deliveryRegistry.getReceipt(tradeId, sellerNodeId);
    expect(storedReceipt.nodeId).to.equal(sellerNodeId);
    expect(storedReceipt.meterSnapshotHash).to.equal(meterSnapshotHash);
    expect(storedReceipt.exists).to.be.true;
  });

  it("3. submitReceipt rejects invalid signature (wrong signer)", async function () {
    const meterSnapshotHash = ethers.keccak256(ethers.toUtf8Bytes("meter-reading-789"));
    const deliveredKwhBucket = 10;
    const periodStart = Math.floor(Date.now() / 1000);
    const periodEnd = periodStart + 3600;
    const nonce = 0n;

    const receipt = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce,
    };

    // Sign with wrong signer (outsider, not the node owner)
    const signature = await signDeliveryReceipt(
      outsider,
      await deliveryRegistry.getAddress(),
      receipt
    );

    await expect(
      deliveryRegistry.submitReceipt(
        tradeId,
        sellerNodeId,
        meterSnapshotHash,
        deliveredKwhBucket,
        periodStart,
        periodEnd,
        nonce,
        signature
      )
    ).to.be.revertedWithCustomError(deliveryRegistry, "InvalidDeliverySignature");
  });

  it("4. submitReceipt rejects replay (wrong nonce)", async function () {
    const meterSnapshotHash = ethers.keccak256(ethers.toUtf8Bytes("meter-reading-aaa"));
    const deliveredKwhBucket = 10;
    const periodStart = Math.floor(Date.now() / 1000);
    const periodEnd = periodStart + 3600;

    // First submission with correct nonce
    const nonce0 = 0n;
    const receipt0 = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce: nonce0,
    };
    const sig0 = await signDeliveryReceipt(
      seller,
      await deliveryRegistry.getAddress(),
      receipt0
    );
    await deliveryRegistry.submitReceipt(
      tradeId, sellerNodeId, meterSnapshotHash, deliveredKwhBucket, periodStart, periodEnd, nonce0, sig0
    );

    // Create a new trade for second submission
    const matchHash2 = ethers.keccak256(ethers.toUtf8Bytes("match-2"));
    const tradeId2 = ethers.solidityPackedKeccak256(
      ["bytes32", "bytes32", "bytes32", "uint32"],
      [matchHash2, buyerNodeId, sellerNodeId, 2]
    );
    await settlementSC.connect(admin).proposeTrade(
      tradeId2, matchHash2, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );

    // Try to submit with old nonce (replay attack)
    const receiptReplay = {
      tradeId: tradeId2,
      nodeId: sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce: nonce0, // OLD nonce
    };
    const sigReplay = await signDeliveryReceipt(
      seller,
      await deliveryRegistry.getAddress(),
      receiptReplay
    );

    await expect(
      deliveryRegistry.submitReceipt(
        tradeId2, sellerNodeId, meterSnapshotHash, deliveredKwhBucket, periodStart, periodEnd, nonce0, sigReplay
      )
    ).to.be.revertedWithCustomError(deliveryRegistry, "InvalidDeliveryNonce");
  });

  it("5. submitReceipt rejects duplicate receipt from same node", async function () {
    const meterSnapshotHash = ethers.keccak256(ethers.toUtf8Bytes("meter-reading-bbb"));
    const deliveredKwhBucket = 10;
    const periodStart = Math.floor(Date.now() / 1000);
    const periodEnd = periodStart + 3600;
    const nonce = 0n;

    const receipt = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce,
    };

    const signature = await signDeliveryReceipt(
      seller,
      await deliveryRegistry.getAddress(),
      receipt
    );

    // First submission
    await deliveryRegistry.submitReceipt(
      tradeId, sellerNodeId, meterSnapshotHash, deliveredKwhBucket, periodStart, periodEnd, nonce, signature
    );

    // Second submission from same node (duplicate)
    const nonce1 = 1n;
    const receipt2 = { ...receipt, nonce: nonce1 };
    const sig2 = await signDeliveryReceipt(
      seller,
      await deliveryRegistry.getAddress(),
      receipt2
    );

    await expect(
      deliveryRegistry.submitReceipt(
        tradeId, sellerNodeId, meterSnapshotHash, deliveredKwhBucket, periodStart, periodEnd, nonce1, sig2
      )
    ).to.be.revertedWithCustomError(deliveryRegistry, "ReceiptAlreadySubmitted");
  });

  // ─────────────────────────────────────────────────────────────────
  //                     Delivery confirmation test (6)
  // ─────────────────────────────────────────────────────────────────

  it("6. sufficient receipts triggers DeliveryConfirmed + calls markDelivered", async function () {
    // Verify trade is in Locked state
    let trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(1); // Locked

    const meterSnapshotHash = ethers.keccak256(ethers.toUtf8Bytes("meter-reading-ccc"));
    const deliveredKwhBucket = 10;
    const periodStart = Math.floor(Date.now() / 1000);
    const periodEnd = periodStart + 3600;
    const nonce = 0n;

    const receipt = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce,
    };

    const signature = await signDeliveryReceipt(
      seller,
      await deliveryRegistry.getAddress(),
      receipt
    );

    // Submit receipt (with REQUIRED_RECEIPTS=1, this should trigger confirmation)
    await expect(
      deliveryRegistry.submitReceipt(
        tradeId, sellerNodeId, meterSnapshotHash, deliveredKwhBucket, periodStart, periodEnd, nonce, signature
      )
    ).to.emit(deliveryRegistry, "DeliveryConfirmed");

    // Verify trade status changed to Delivered
    trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(2); // Delivered
  });

  // ─────────────────────────────────────────────────────────────────
  //                     View helper test (7)
  // ─────────────────────────────────────────────────────────────────

  it("7. getReceipt view helper returns correct data", async function () {
    const meterSnapshotHash = ethers.keccak256(ethers.toUtf8Bytes("meter-reading-ddd"));
    const deliveredKwhBucket = 25;
    const periodStart = Math.floor(Date.now() / 1000);
    const periodEnd = periodStart + 7200;
    const nonce = 0n;

    const receipt = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce,
    };

    const signature = await signDeliveryReceipt(
      seller,
      await deliveryRegistry.getAddress(),
      receipt
    );

    await deliveryRegistry.submitReceipt(
      tradeId, sellerNodeId, meterSnapshotHash, deliveredKwhBucket, periodStart, periodEnd, nonce, signature
    );

    // Use view helper
    const storedReceipt = await deliveryRegistry.getReceipt(tradeId, sellerNodeId);
    expect(storedReceipt.nodeId).to.equal(sellerNodeId);
    expect(storedReceipt.meterSnapshotHash).to.equal(meterSnapshotHash);
    expect(storedReceipt.deliveredKwhBucket).to.equal(deliveredKwhBucket);
    expect(storedReceipt.periodStart).to.equal(periodStart);
    expect(storedReceipt.periodEnd).to.equal(periodEnd);
    expect(storedReceipt.exists).to.be.true;

    // Verify receipt count
    expect(await deliveryRegistry.getReceiptCount(tradeId)).to.equal(1);

    // Verify nonce incremented
    expect(await deliveryRegistry.getDeliveryNonce(sellerNodeId)).to.equal(1);
  });

  // ─────────────────────────────────────────────────────────────────
  //                     Additional tests
  // ─────────────────────────────────────────────────────────────────

  it("rejects receipt from inactive node", async function () {
    // Revoke seller node
    await identitySC.connect(admin).revokeNode(sellerNodeId);

    const meterSnapshotHash = ethers.keccak256(ethers.toUtf8Bytes("meter-reading-eee"));
    const deliveredKwhBucket = 10;
    const periodStart = Math.floor(Date.now() / 1000);
    const periodEnd = periodStart + 3600;
    const nonce = 0n;

    const receipt = {
      tradeId,
      nodeId: sellerNodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      nonce,
    };

    const signature = await signDeliveryReceipt(
      seller,
      await deliveryRegistry.getAddress(),
      receipt
    );

    await expect(
      deliveryRegistry.submitReceipt(
        tradeId, sellerNodeId, meterSnapshotHash, deliveredKwhBucket, periodStart, periodEnd, nonce, signature
      )
    ).to.be.revertedWithCustomError(deliveryRegistry, "NodeNotActive");
  });
});
