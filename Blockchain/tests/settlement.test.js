/**
 * tests/settlement.test.js — SettlementSC unit tests (Hardhat + Chai)
 *
 * Covers:
 *   1.  proposeTrade success - stores trade, locks funds, emits TradeProposed
 *   2.  proposeTrade rejects duplicate tradeId
 *   3.  proposeTrade rejects unauthorized caller (role check)
 *   4.  proposeTrade rejects insufficient collateral
 *   5.  markDelivered success - updates status, emits DeliveryMarked
 *   6.  markDelivered rejects unauthorized caller (role check)
 *   7.  executeSettlement success - transfers funds to seller
 *   8.  executeSettlement rejects wrong status (not Delivered)
 *   9.  executeSettlement rejects before dispute window closes
 *  10.  refundTrade success - returns funds to buyer after timeout
 *  11.  refundTrade rejects before timeout
 *  12.  disputeTrade + resolveDispute full lifecycle
 */
const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("SettlementSC", function () {
  let mockUSDC, identitySC, collateralSC, settlementSC;
  let admin, buyer, seller, relayer, outsider;

  const USDC_DECIMALS = 6;
  const ONE_USDC = 10n ** BigInt(USDC_DECIMALS);
  const DEPOSIT_AMOUNT = 1000n * ONE_USDC;
  const TRADE_AMOUNT = 100n * ONE_USDC;
  const SETTLEMENT_TIMEOUT = 120; // blocks
  const DISPUTE_WINDOW = 60; // blocks

  let buyerNodeId, sellerNodeId;
  let matchHash, tradeId;

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

    // Grant SETTLEMENT_ROLE to SettlementSC on CollateralSC
    const SETTLEMENT_ROLE = await collateralSC.SETTLEMENT_ROLE();
    await collateralSC.connect(admin).grantRole(SETTLEMENT_ROLE, await settlementSC.getAddress());

    // Grant MATCH_REGISTRY_ROLE to admin for testing
    const MATCH_REGISTRY_ROLE = await settlementSC.MATCH_REGISTRY_ROLE();
    await settlementSC.connect(admin).grantRole(MATCH_REGISTRY_ROLE, admin.address);

    // Grant DELIVERY_REGISTRY_ROLE to admin for testing
    const DELIVERY_REGISTRY_ROLE = await settlementSC.DELIVERY_REGISTRY_ROLE();
    await settlementSC.connect(admin).grantRole(DELIVERY_REGISTRY_ROLE, admin.address);

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

    // Prepare trade identifiers
    matchHash = ethers.keccak256(ethers.toUtf8Bytes("match-1"));
    tradeId = ethers.solidityPackedKeccak256(
      ["bytes32", "bytes32", "bytes32", "uint32"],
      [matchHash, buyerNodeId, sellerNodeId, 1]
    );
  });

  // ─────────────────────────────────────────────────────────────────
  //                     proposeTrade tests (1-4)
  // ─────────────────────────────────────────────────────────────────

  it("1. proposeTrade success - stores trade, locks funds, emits TradeProposed", async function () {
    const depositBefore = await collateralSC.getDeposit(buyerNodeId);

    await expect(
      settlementSC.connect(admin).proposeTrade(
        tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
      )
    )
      .to.emit(settlementSC, "TradeProposed")
      .withArgs(tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT);

    // Verify trade stored
    const trade = await settlementSC.getTrade(tradeId);
    expect(trade.matchHash).to.equal(matchHash);
    expect(trade.buyerNodeId).to.equal(buyerNodeId);
    expect(trade.sellerNodeId).to.equal(sellerNodeId);
    expect(trade.lockedAmount).to.equal(TRADE_AMOUNT);
    expect(trade.status).to.equal(1); // Locked

    // Verify funds locked
    const depositAfter = await collateralSC.getDeposit(buyerNodeId);
    expect(depositAfter).to.equal(depositBefore - TRADE_AMOUNT);
    expect(await collateralSC.tradeLocks(tradeId)).to.equal(TRADE_AMOUNT);
  });

  it("2. proposeTrade rejects duplicate tradeId", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );

    await expect(
      settlementSC.connect(admin).proposeTrade(
        tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
      )
    ).to.be.revertedWithCustomError(settlementSC, "TradeAlreadyExists");
  });

  it("3. proposeTrade rejects unauthorized caller (role check)", async function () {
    await expect(
      settlementSC.connect(outsider).proposeTrade(
        tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
      )
    ).to.be.reverted; // AccessControl revert
  });

  it("4. proposeTrade rejects insufficient collateral", async function () {
    const excessiveAmount = DEPOSIT_AMOUNT + ONE_USDC;

    await expect(
      settlementSC.connect(admin).proposeTrade(
        tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, excessiveAmount
      )
    ).to.be.revertedWithCustomError(collateralSC, "InsufficientDeposit");
  });

  // ─────────────────────────────────────────────────────────────────
  //                     markDelivered tests (5-6)
  // ─────────────────────────────────────────────────────────────────

  it("5. markDelivered success - updates status, emits DeliveryMarked", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );

    await expect(settlementSC.connect(admin).markDelivered(tradeId))
      .to.emit(settlementSC, "DeliveryMarked");

    const trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(2); // Delivered
    expect(trade.deliveredBlock).to.be.gt(0);
  });

  it("6. markDelivered rejects unauthorized caller (role check)", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );

    await expect(
      settlementSC.connect(outsider).markDelivered(tradeId)
    ).to.be.reverted; // AccessControl revert
  });

  // ─────────────────────────────────────────────────────────────────
  //                     executeSettlement tests (7-9)
  // ─────────────────────────────────────────────────────────────────

  it("7. executeSettlement success - transfers funds to seller", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );
    await settlementSC.connect(admin).markDelivered(tradeId);

    // Mine past dispute window
    for (let i = 0; i < DISPUTE_WINDOW + 1; i++) {
      await ethers.provider.send("evm_mine", []);
    }

    const sellerBalBefore = await mockUSDC.balanceOf(seller.address);

    await expect(settlementSC.connect(admin).executeSettlement(tradeId))
      .to.emit(settlementSC, "SettlementCompleted")
      .withArgs(tradeId, TRADE_AMOUNT);

    // Verify funds transferred to seller
    const sellerBalAfter = await mockUSDC.balanceOf(seller.address);
    expect(sellerBalAfter).to.equal(sellerBalBefore + TRADE_AMOUNT);

    // Verify trade settled
    const trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(3); // Settled
  });

  it("8. executeSettlement rejects wrong status (not Delivered)", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );
    // Trade is Locked, not Delivered

    await expect(
      settlementSC.connect(admin).executeSettlement(tradeId)
    ).to.be.revertedWithCustomError(settlementSC, "InvalidTradeStatus");
  });

  it("9. executeSettlement rejects before dispute window closes", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );
    await settlementSC.connect(admin).markDelivered(tradeId);
    // Don't mine - dispute window still open

    await expect(
      settlementSC.connect(admin).executeSettlement(tradeId)
    ).to.be.revertedWithCustomError(settlementSC, "DisputeWindowNotPassed");
  });

  // ─────────────────────────────────────────────────────────────────
  //                     refundTrade tests (10-11)
  // ─────────────────────────────────────────────────────────────────

  it("10. refundTrade success - returns funds to buyer after timeout", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );

    // Mine past settlement timeout
    for (let i = 0; i < SETTLEMENT_TIMEOUT + 1; i++) {
      await ethers.provider.send("evm_mine", []);
    }

    const depositBefore = await collateralSC.getDeposit(buyerNodeId);

    await expect(settlementSC.connect(outsider).refundTrade(tradeId))
      .to.emit(settlementSC, "TradeRefunded")
      .withArgs(tradeId, buyerNodeId, TRADE_AMOUNT);

    // Verify funds returned to buyer deposit
    const depositAfter = await collateralSC.getDeposit(buyerNodeId);
    expect(depositAfter).to.equal(depositBefore + TRADE_AMOUNT);

    // Verify trade refunded
    const trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(5); // Refunded
  });

  it("11. refundTrade rejects before timeout", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );
    // Don't mine - timeout not reached

    await expect(
      settlementSC.refundTrade(tradeId)
    ).to.be.revertedWithCustomError(settlementSC, "SettlementTimeoutNotReached");
  });

  // ─────────────────────────────────────────────────────────────────
  //                     Dispute lifecycle test (12)
  // ─────────────────────────────────────────────────────────────────

  it("12. disputeTrade + resolveDispute full lifecycle", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );
    await settlementSC.connect(admin).markDelivered(tradeId);

    // Buyer disputes within window
    await expect(settlementSC.connect(buyer).disputeTrade(tradeId))
      .to.emit(settlementSC, "TradeDisputed")
      .withArgs(tradeId, buyer.address);

    let trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(4); // Disputed

    // Adjudicator resolves in favor of seller (sellerFavored=true)
    const sellerBalBefore = await mockUSDC.balanceOf(seller.address);

    await expect(
      settlementSC.connect(admin).resolveDispute(tradeId, true, TRADE_AMOUNT, 0)
    )
      .to.emit(settlementSC, "DisputeResolved")
      .withArgs(tradeId, true, TRADE_AMOUNT, 0);

    // Seller receives funds
    const sellerBalAfter = await mockUSDC.balanceOf(seller.address);
    expect(sellerBalAfter).to.equal(sellerBalBefore + TRADE_AMOUNT);

    // Trade status is Settled
    trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(3); // Settled
  });

  // ─────────────────────────────────────────────────────────────────
  //                     Additional edge case tests
  // ─────────────────────────────────────────────────────────────────

  it("12b. resolveDispute in favor of buyer refunds correctly", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );
    await settlementSC.connect(admin).markDelivered(tradeId);
    await settlementSC.connect(buyer).disputeTrade(tradeId);

    const buyerDepositBefore = await collateralSC.getDeposit(buyerNodeId);

    // Resolve in favor of buyer
    await settlementSC.connect(admin).resolveDispute(tradeId, true, 0, TRADE_AMOUNT);

    // Buyer deposit restored
    const buyerDepositAfter = await collateralSC.getDeposit(buyerNodeId);
    expect(buyerDepositAfter).to.equal(buyerDepositBefore + TRADE_AMOUNT);

    // Trade status is Settled (dispute resolved)
    const trade = await settlementSC.getTrade(tradeId);
    expect(trade.status).to.equal(3); // Settled
  });

  it("disputeTrade rejects after dispute window closes", async function () {
    await settlementSC.connect(admin).proposeTrade(
      tradeId, matchHash, buyerNodeId, sellerNodeId, 10, 50, TRADE_AMOUNT
    );
    await settlementSC.connect(admin).markDelivered(tradeId);

    // Mine past dispute window
    for (let i = 0; i < DISPUTE_WINDOW + 1; i++) {
      await ethers.provider.send("evm_mine", []);
    }

    await expect(
      settlementSC.connect(buyer).disputeTrade(tradeId)
    ).to.be.revertedWithCustomError(settlementSC, "DisputeWindowClosed");
  });
});
