// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title  SettlementSC — Grid-Guardian Trade Settlement Orchestrator
 * @notice Manages the full trade lifecycle after match finalization:
 *         propose → lock funds → delivery confirmed → settle (or timeout → refund, or dispute).
 *
 * Flow:
 *   1. After MatchRegistry.finalizeMatch(), relayer calls proposeTrade() for each trade.
 *   2. proposeTrade() locks buyer collateral via CollateralSC.lockFunds().
 *   3. DeliveryRegistry confirms delivery → markDelivered().
 *   4. After dispute window, executeSettlement() transfers funds to seller.
 *   5. If no delivery within settlementTimeout, refundTrade() returns funds to buyer.
 *   6. If disputed, adjudicator resolves via resolveDispute().
 */

interface ICollateralSC_Settlement {
    function lockFunds(bytes32 nodeId, uint256 amount, bytes32 tradeId) external;
    function unlockFunds(bytes32 tradeId) external;
    function transferLockedFunds(bytes32 tradeId, address to) external;
}

interface IIdentitySC_Settlement {
    function nodes(bytes32 nodeId) external view returns (
        address owner,
        bytes32 pubkeyHash,
        string memory metaURI,
        uint256 stake,
        uint256 registeredAt,
        bool active,
        bool attested
    );
}

contract SettlementSC is AccessControl, ReentrancyGuard {
    // ── Roles ──────────────────────────────────────────────────────────
    bytes32 public constant ADMIN_ROLE            = keccak256("ADMIN_ROLE");
    bytes32 public constant MATCH_REGISTRY_ROLE   = keccak256("MATCH_REGISTRY_ROLE");
    bytes32 public constant DELIVERY_REGISTRY_ROLE = keccak256("DELIVERY_REGISTRY_ROLE");
    bytes32 public constant ADJUDICATOR_ROLE      = keccak256("ADJUDICATOR_ROLE");

    // ── External references ────────────────────────────────────────────
    ICollateralSC_Settlement public immutable collateralSC;
    IIdentitySC_Settlement  public immutable identitySC;

    // ── Configuration ──────────────────────────────────────────────────
    uint256 public settlementTimeout;   // blocks until refund allowed
    uint256 public disputeWindow;       // blocks after delivery for dispute

    // ── Trade lifecycle ────────────────────────────────────────────────
    enum TradeStatus { None, Locked, Delivered, Settled, Disputed, Refunded }

    struct Trade {
        bytes32     matchHash;
        bytes32     buyerNodeId;
        bytes32     sellerNodeId;
        uint16      kwhBucket;
        uint16      priceBucket;
        uint256     lockedAmount;
        uint256     proposedBlock;
        uint256     deliveredBlock;
        TradeStatus status;
    }

    mapping(bytes32 => Trade) public trades;
    uint256 public tradeCount;

    // ── Events ─────────────────────────────────────────────────────────
    event TradeProposed(
        bytes32 indexed tradeId,
        bytes32 indexed matchHash,
        bytes32 buyerNodeId,
        bytes32 sellerNodeId,
        uint16 kwhBucket,
        uint16 priceBucket,
        uint256 lockedAmount
    );
    event FundsLocked(bytes32 indexed tradeId, bytes32 indexed buyerNodeId, uint256 amount);
    event TradeExecuted(
        bytes32 indexed tradeId,
        bytes32 indexed buyerNodeId,
        bytes32 indexed sellerNodeId,
        uint16 kwhBucket,
        uint16 priceBucket
    );
    event DeliveryMarked(bytes32 indexed tradeId, uint256 deliveredBlock);
    event SettlementCompleted(bytes32 indexed tradeId, uint256 amount);
    event TradeRefunded(bytes32 indexed tradeId, bytes32 indexed buyerNodeId, uint256 amount);
    event TradeDisputed(bytes32 indexed tradeId, address indexed disputant);
    event DisputeResolved(
        bytes32 indexed tradeId,
        bool sellerFavored,
        uint256 sellerAmount,
        uint256 buyerRefund
    );
    event ConfigUpdated(string param, uint256 newValue);

    // ── Errors ─────────────────────────────────────────────────────────
    error TradeAlreadyExists(bytes32 tradeId);
    error TradeNotFound(bytes32 tradeId);
    error InvalidTradeStatus(bytes32 tradeId, TradeStatus current, TradeStatus expected);
    error SettlementTimeoutNotReached(bytes32 tradeId);
    error DisputeWindowNotPassed(bytes32 tradeId);
    error DisputeWindowClosed(bytes32 tradeId);
    error AmountsExceedLocked();

    // ── Constructor ────────────────────────────────────────────────────
    constructor(
        address _collateralSC,
        address _identitySC,
        address admin,
        uint256 _settlementTimeout,
        uint256 _disputeWindow
    ) {
        collateralSC = ICollateralSC_Settlement(_collateralSC);
        identitySC   = IIdentitySC_Settlement(_identitySC);
        settlementTimeout = _settlementTimeout;
        disputeWindow     = _disputeWindow;

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ADMIN_ROLE, admin);
        _grantRole(ADJUDICATOR_ROLE, admin);
    }

    // ════════════════════════════════════════════════════════════════════
    //                      PROPOSE TRADE
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Propose and lock a trade after match finalization.
     * @param tradeId      Deterministic trade identifier
     * @param matchHash    The finalized match this trade belongs to
     * @param buyerNodeId  Buyer's registered node ID
     * @param sellerNodeId Seller's registered node ID
     * @param kwhBucket    Quantized kWh bucket
     * @param priceBucket  Quantized price bucket
     * @param amount       USDC amount to lock from buyer's collateral
     */
    function proposeTrade(
        bytes32 tradeId,
        bytes32 matchHash,
        bytes32 buyerNodeId,
        bytes32 sellerNodeId,
        uint16  kwhBucket,
        uint16  priceBucket,
        uint256 amount
    ) external onlyRole(MATCH_REGISTRY_ROLE) nonReentrant {
        if (trades[tradeId].status != TradeStatus.None)
            revert TradeAlreadyExists(tradeId);

        // Lock buyer funds via CollateralSC
        collateralSC.lockFunds(buyerNodeId, amount, tradeId);

        trades[tradeId] = Trade({
            matchHash:     matchHash,
            buyerNodeId:   buyerNodeId,
            sellerNodeId:  sellerNodeId,
            kwhBucket:     kwhBucket,
            priceBucket:   priceBucket,
            lockedAmount:  amount,
            proposedBlock: block.number,
            deliveredBlock: 0,
            status:        TradeStatus.Locked
        });
        tradeCount++;

        emit TradeProposed(tradeId, matchHash, buyerNodeId, sellerNodeId, kwhBucket, priceBucket, amount);
        emit FundsLocked(tradeId, buyerNodeId, amount);
        emit TradeExecuted(tradeId, buyerNodeId, sellerNodeId, kwhBucket, priceBucket);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     DELIVERY CONFIRMATION
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Mark trade as delivered (called by DeliveryRegistry).
     * @param tradeId The trade to mark as delivered
     */
    function markDelivered(bytes32 tradeId) external onlyRole(DELIVERY_REGISTRY_ROLE) {
        Trade storage t = trades[tradeId];
        if (t.status != TradeStatus.Locked)
            revert InvalidTradeStatus(tradeId, t.status, TradeStatus.Locked);

        t.status = TradeStatus.Delivered;
        t.deliveredBlock = block.number;

        emit DeliveryMarked(tradeId, block.number);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     SETTLEMENT EXECUTION
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Execute settlement — transfer funds to seller after dispute window.
     * @param tradeId The trade to settle
     */
    function executeSettlement(bytes32 tradeId) external onlyRole(ADMIN_ROLE) nonReentrant {
        Trade storage t = trades[tradeId];
        if (t.status != TradeStatus.Delivered)
            revert InvalidTradeStatus(tradeId, t.status, TradeStatus.Delivered);
        if (block.number <= t.deliveredBlock + disputeWindow)
            revert DisputeWindowNotPassed(tradeId);

        // Get seller address from IdentitySC
        (address sellerAddr, , , , , , ) = identitySC.nodes(t.sellerNodeId);

        t.status = TradeStatus.Settled;
        collateralSC.transferLockedFunds(tradeId, sellerAddr);

        emit SettlementCompleted(tradeId, t.lockedAmount);
    }

    // ════════════════════════════════════════════════════════════════════
    //                        REFUND (TIMEOUT)
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Refund buyer if no delivery within settlement timeout.
     * @param tradeId The trade to refund
     */
    function refundTrade(bytes32 tradeId) external nonReentrant {
        Trade storage t = trades[tradeId];
        if (t.status != TradeStatus.Locked)
            revert InvalidTradeStatus(tradeId, t.status, TradeStatus.Locked);
        if (block.number <= t.proposedBlock + settlementTimeout)
            revert SettlementTimeoutNotReached(tradeId);

        t.status = TradeStatus.Refunded;
        collateralSC.unlockFunds(tradeId);

        emit TradeRefunded(tradeId, t.buyerNodeId, t.lockedAmount);
    }

    // ════════════════════════════════════════════════════════════════════
    //                          DISPUTES
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Dispute a trade within the dispute window after delivery.
     * @param tradeId The trade to dispute
     */
    function disputeTrade(bytes32 tradeId) external {
        Trade storage t = trades[tradeId];
        if (t.status != TradeStatus.Delivered)
            revert InvalidTradeStatus(tradeId, t.status, TradeStatus.Delivered);
        if (block.number > t.deliveredBlock + disputeWindow)
            revert DisputeWindowClosed(tradeId);

        t.status = TradeStatus.Disputed;
        emit TradeDisputed(tradeId, msg.sender);
    }

    /**
     * @notice Adjudicator resolves a dispute.
     * @param tradeId       The disputed trade
     * @param sellerFavored True if seller should receive funds
     * @param sellerAmount  Amount for seller (if seller favored)
     * @param buyerRefund   Amount refunded to buyer (if buyer favored)
     */
    function resolveDispute(
        bytes32 tradeId,
        bool    sellerFavored,
        uint256 sellerAmount,
        uint256 buyerRefund
    ) external onlyRole(ADJUDICATOR_ROLE) nonReentrant {
        Trade storage t = trades[tradeId];
        if (t.status != TradeStatus.Disputed)
            revert InvalidTradeStatus(tradeId, t.status, TradeStatus.Disputed);
        if (sellerAmount + buyerRefund > t.lockedAmount)
            revert AmountsExceedLocked();

        t.status = TradeStatus.Settled;

        if (sellerFavored && sellerAmount > 0) {
            (address sellerAddr, , , , , , ) = identitySC.nodes(t.sellerNodeId);
            collateralSC.transferLockedFunds(tradeId, sellerAddr);
        } else {
            collateralSC.unlockFunds(tradeId);
        }

        emit DisputeResolved(tradeId, sellerFavored, sellerAmount, buyerRefund);
    }

    // ════════════════════════════════════════════════════════════════════
    //                        VIEW HELPERS
    // ════════════════════════════════════════════════════════════════════

    function getTrade(bytes32 tradeId) external view returns (Trade memory) {
        return trades[tradeId];
    }

    // ════════════════════════════════════════════════════════════════════
    //                     ADMIN CONFIGURATION
    // ════════════════════════════════════════════════════════════════════

    function setSettlementTimeout(uint256 blocks_) external onlyRole(ADMIN_ROLE) {
        settlementTimeout = blocks_;
        emit ConfigUpdated("settlementTimeout", blocks_);
    }

    function setDisputeWindow(uint256 blocks_) external onlyRole(ADMIN_ROLE) {
        disputeWindow = blocks_;
        emit ConfigUpdated("disputeWindow", blocks_);
    }
}
