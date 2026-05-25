// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title  DeliveryRegistry — Grid-Guardian Delivery Receipt Verification
 * @notice Accepts EIP-712 signed delivery receipts from Pi edge nodes,
 *         verifies signatures against IdentitySC node ownership,
 *         and triggers SettlementSC.markDelivered() when sufficient
 *         receipts are collected.
 *
 * Flow:
 *   1. Pi reads meter after energy delivery.
 *   2. Pi signs a DeliveryReceipt (EIP-712) with trade details.
 *   3. Relayer submits receipt via submitReceipt().
 *   4. Contract verifies signature, stores receipt, increments count.
 *   5. When receiptCount >= requiredReceipts, calls markDelivered().
 */

interface IIdentitySC_Delivery {
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

interface ISettlementSC_Delivery {
    function markDelivered(bytes32 tradeId) external;
}

contract DeliveryRegistry is AccessControl, EIP712, ReentrancyGuard {
    using ECDSA for bytes32;

    // ── Roles ──────────────────────────────────────────────────────────
    bytes32 public constant ADMIN_ROLE   = keccak256("ADMIN_ROLE");
    bytes32 public constant RELAYER_ROLE = keccak256("RELAYER_ROLE");

    // ── EIP-712 DeliveryReceipt typehash ─────────────────────────────
    bytes32 public constant DELIVERY_TYPEHASH = keccak256(
        "DeliveryReceipt(bytes32 tradeId,bytes32 nodeId,bytes32 meterSnapshotHash,uint16 deliveredKwhBucket,uint256 periodStart,uint256 periodEnd,uint256 nonce)"
    );

    // ── External references ────────────────────────────────────────────
    IIdentitySC_Delivery   public immutable identitySC;
    ISettlementSC_Delivery public immutable settlementSC;

    // ── Configuration ──────────────────────────────────────────────────
    uint256 public requiredReceipts;   // 1 = single-party, 2 = dual-party

    // ── Storage ────────────────────────────────────────────────────────
    struct Receipt {
        bytes32 nodeId;
        bytes32 meterSnapshotHash;
        uint16  deliveredKwhBucket;
        uint256 periodStart;
        uint256 periodEnd;
        uint256 submittedBlock;
        bool    exists;
    }

    // tradeId => nodeId => Receipt
    mapping(bytes32 => mapping(bytes32 => Receipt)) public receipts;
    // tradeId => count of receipts submitted
    mapping(bytes32 => uint256) public receiptCount;
    // nodeId => nonce for replay protection
    mapping(bytes32 => uint256) public deliveryNonce;

    // ── Events ─────────────────────────────────────────────────────────
    event DeliveryReceiptSubmitted(
        bytes32 indexed tradeId,
        bytes32 indexed nodeId,
        bytes32 meterSnapshotHash,
        uint16 deliveredKwhBucket,
        uint256 submittedBlock
    );
    event DeliveryConfirmed(bytes32 indexed tradeId, uint256 totalReceipts);

    // ── Errors ─────────────────────────────────────────────────────────
    error InvalidDeliveryNonce(bytes32 nodeId, uint256 expected, uint256 got);
    error InvalidDeliverySignature();
    error NodeNotActive(bytes32 nodeId);
    error ReceiptAlreadySubmitted(bytes32 tradeId, bytes32 nodeId);

    // ── Constructor ────────────────────────────────────────────────────
    constructor(
        address _identitySC,
        address _settlementSC,
        address admin,
        uint256 _requiredReceipts
    ) EIP712("GridGuardian-Delivery", "1") {
        identitySC       = IIdentitySC_Delivery(_identitySC);
        settlementSC     = ISettlementSC_Delivery(_settlementSC);
        requiredReceipts = _requiredReceipts;

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ADMIN_ROLE, admin);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     SUBMIT DELIVERY RECEIPT
    // ════════════════════════════════════════════════════════════════════

    // ── Input struct to reduce stack variables ──
    struct ReceiptInput {
        bytes32 tradeId;
        bytes32 nodeId;
        bytes32 meterSnapshotHash;
        uint16  deliveredKwhBucket;
        uint256 periodStart;
        uint256 periodEnd;
        uint256 nonce;
    }

    /**
     * @notice Submit a signed delivery receipt from a Pi node.
     * @param tradeId            The trade this receipt is for
     * @param nodeId             The node submitting the receipt
     * @param meterSnapshotHash  keccak256(meter_reading || timestamp || nodeId)
     * @param deliveredKwhBucket Quantized delivered kWh bucket
     * @param periodStart        Delivery period start timestamp
     * @param periodEnd          Delivery period end timestamp
     * @param nonce              Per-node monotonic nonce
     * @param signature          EIP-712 signature from the node owner
     */
    function submitReceipt(
        bytes32 tradeId,
        bytes32 nodeId,
        bytes32 meterSnapshotHash,
        uint16  deliveredKwhBucket,
        uint256 periodStart,
        uint256 periodEnd,
        uint256 nonce,
        bytes calldata signature
    ) external nonReentrant {
        ReceiptInput memory input = ReceiptInput({
            tradeId: tradeId,
            nodeId: nodeId,
            meterSnapshotHash: meterSnapshotHash,
            deliveredKwhBucket: deliveredKwhBucket,
            periodStart: periodStart,
            periodEnd: periodEnd,
            nonce: nonce
        });
        _processReceipt(input, signature);
    }

    function _processReceipt(ReceiptInput memory input, bytes calldata signature) internal {
        // ── 1. Check nonce ──
        if (input.nonce != deliveryNonce[input.nodeId])
            revert InvalidDeliveryNonce(input.nodeId, deliveryNonce[input.nodeId], input.nonce);

        // ── 2. Verify signature and node ──
        _verifySignature(input, signature);

        // ── 3. Check no duplicate receipt ──
        if (receipts[input.tradeId][input.nodeId].exists)
            revert ReceiptAlreadySubmitted(input.tradeId, input.nodeId);

        // ── 4. Store receipt ──
        _storeReceipt(input);
    }

    function _verifySignature(ReceiptInput memory input, bytes calldata signature) internal view {
        bytes32 structHash = keccak256(abi.encode(
            DELIVERY_TYPEHASH,
            input.tradeId,
            input.nodeId,
            input.meterSnapshotHash,
            input.deliveredKwhBucket,
            input.periodStart,
            input.periodEnd,
            input.nonce
        ));
        bytes32 digest = _hashTypedDataV4(structHash);
        address signer = ECDSA.recover(digest, signature);

        (address owner, , , , , bool active, ) = identitySC.nodes(input.nodeId);
        if (!active) revert NodeNotActive(input.nodeId);
        if (signer != owner) revert InvalidDeliverySignature();
    }

    function _storeReceipt(ReceiptInput memory input) internal {
        deliveryNonce[input.nodeId]++;

        Receipt storage r = receipts[input.tradeId][input.nodeId];
        r.nodeId = input.nodeId;
        r.meterSnapshotHash = input.meterSnapshotHash;
        r.deliveredKwhBucket = input.deliveredKwhBucket;
        r.periodStart = input.periodStart;
        r.periodEnd = input.periodEnd;
        r.submittedBlock = block.number;
        r.exists = true;

        receiptCount[input.tradeId]++;

        emit DeliveryReceiptSubmitted(input.tradeId, input.nodeId, input.meterSnapshotHash, input.deliveredKwhBucket, block.number);

        // If sufficient receipts, confirm delivery
        if (receiptCount[input.tradeId] >= requiredReceipts) {
            settlementSC.markDelivered(input.tradeId);
            emit DeliveryConfirmed(input.tradeId, receiptCount[input.tradeId]);
        }
    }

    // ════════════════════════════════════════════════════════════════════
    //                        VIEW HELPERS
    // ════════════════════════════════════════════════════════════════════

    function getReceipt(bytes32 tradeId, bytes32 nodeId) external view returns (Receipt memory) {
        return receipts[tradeId][nodeId];
    }

    function getReceiptCount(bytes32 tradeId) external view returns (uint256) {
        return receiptCount[tradeId];
    }

    function getDeliveryNonce(bytes32 nodeId) external view returns (uint256) {
        return deliveryNonce[nodeId];
    }

    function getDomainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }

    // ════════════════════════════════════════════════════════════════════
    //                     ADMIN CONFIGURATION
    // ════════════════════════════════════════════════════════════════════

    function setRequiredReceipts(uint256 count) external onlyRole(ADMIN_ROLE) {
        requiredReceipts = count;
    }
}
