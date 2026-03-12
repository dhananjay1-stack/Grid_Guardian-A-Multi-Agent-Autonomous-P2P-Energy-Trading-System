// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title  TradingSC — Grid-Guardian Commit–Reveal Offer Contract
 * @notice Handles commit–reveal flow for energy trading offers:
 *   1. Pi composes a quantized offer (buckets for kWh & price).
 *   2. Pi signs the offer via EIP-712.
 *   3. Pi (or relayer) posts commit = keccak256(offerHash | salt) on-chain.
 *   4. Off-chain P2P broadcast carries the full offer (without salt).
 *   5. When needed, Pi reveals by providing offerHash + salt.
 *
 * Design:
 *   - Quantized buckets reduce privacy leakage and gas cost.
 *   - Commit hides offer content until reveal window.
 *   - EIP-712 typed signing for unambiguous, replay-protected signatures.
 *   - Anti-spam: optional integration with CollateralSC / IdentitySC.
 */

interface IIdentitySC_Trading {
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

contract TradingSC is EIP712, ReentrancyGuard {
    using ECDSA for bytes32;

    // ── EIP-712 Offer typehash ─────────────────────────────────────────
    bytes32 public constant OFFER_TYPEHASH = keccak256(
        "Offer(uint32 roundId,bytes32 nodeId,uint16 kwhBucket,uint16 priceBucket,uint32 nonce,uint32 expiryBlock)"
    );

    // ── External references ────────────────────────────────────────────
    IIdentitySC_Trading public immutable identitySC;

    // ── Compact commit struct (1 storage slot + 1 bool) ────────────────
    struct Commit {
        address owner;       // signer address (node owner from IdentitySC)
        uint32  roundId;     // market round
        uint32  expiryBlock; // block number after which reveal is not allowed
        bool    revealed;    // has this commit been revealed?
    }

    // commitHash => Commit
    mapping(bytes32 => Commit) public commits;

    // ── Events ─────────────────────────────────────────────────────────
    event CommitPosted(
        bytes32 indexed commitHash,
        address indexed owner,
        uint32 roundId,
        uint32 expiryBlock
    );
    event OfferRevealed(
        bytes32 indexed commitHash,
        bytes32 offerHash,
        bytes32 saltHash
    );

    // ── Errors ─────────────────────────────────────────────────────────
    error CommitAlreadyExists();
    error InvalidSignature();
    error SignerNotRegisteredNode();
    error NoCommitFound();
    error AlreadyRevealed();
    error CommitExpired();
    error CommitHashMismatch();

    // ── Constructor ────────────────────────────────────────────────────
    constructor(address _identitySC)
        EIP712("GridGuardian-Trading", "1")
    {
        identitySC = IIdentitySC_Trading(_identitySC);
    }

    // ════════════════════════════════════════════════════════════════════
    //                         POST COMMIT
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Post a commit on-chain.
     * @param commitHash  keccak256(offerHash | salt) — the hidden commitment
     * @param offerHash   EIP-712 struct hash of the Offer (for signature recovery)
     * @param roundId     Market round identifier
     * @param nodeId      The node's registered ID in IdentitySC
     * @param expiryBlock Block number after which reveal is not accepted
     * @param sig         EIP-712 signature of the Offer struct by the node owner
     */
    function postCommit(
        bytes32 commitHash,
        bytes32 offerHash,
        uint32  roundId,
        bytes32 nodeId,
        uint32  expiryBlock,
        bytes calldata sig
    ) external nonReentrant {
        if (commits[commitHash].owner != address(0)) revert CommitAlreadyExists();

        // ── Recover signer from EIP-712 offer signature ──
        bytes32 digest = _hashTypedDataV4(offerHash);
        address signer = ECDSA.recover(digest, sig);
        if (signer == address(0)) revert InvalidSignature();

        // ── Verify signer is a registered active node ──
        (address nodeOwner, , , , uint256 registeredAt, bool active, ) = identitySC.nodes(nodeId);
        if (registeredAt == 0 || !active || nodeOwner != signer) revert SignerNotRegisteredNode();

        // ── Store commit ──
        commits[commitHash] = Commit({
            owner:       signer,
            roundId:     roundId,
            expiryBlock: expiryBlock,
            revealed:    false
        });

        emit CommitPosted(commitHash, signer, roundId, expiryBlock);
    }

    // ════════════════════════════════════════════════════════════════════
    //                         REVEAL OFFER
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Reveal an offer by providing offerHash and salt.
     *         Verifies keccak256(offerHash | salt) == commitHash.
     * @param commitHash The previously posted commitment
     * @param offerHash  The keccak256 struct hash of the Offer
     * @param salt       The 32-byte salt used to create the commitment
     */
    function revealOffer(
        bytes32 commitHash,
        bytes32 offerHash,
        bytes32 salt
    ) external nonReentrant {
        Commit storage c = commits[commitHash];
        if (c.owner == address(0)) revert NoCommitFound();
        if (c.revealed) revert AlreadyRevealed();
        if (block.number > c.expiryBlock) revert CommitExpired();

        // ── Verify commit integrity ──
        bytes32 computed = keccak256(abi.encodePacked(offerHash, salt));
        if (computed != commitHash) revert CommitHashMismatch();

        // ── Mark revealed ──
        c.revealed = true;

        emit OfferRevealed(commitHash, offerHash, keccak256(abi.encodePacked(salt)));
    }

    // ════════════════════════════════════════════════════════════════════
    //                          VIEW HELPERS
    // ════════════════════════════════════════════════════════════════════

    function isCommitRevealed(bytes32 commitHash) external view returns (bool) {
        return commits[commitHash].revealed;
    }

    function getCommit(bytes32 commitHash)
        external view
        returns (address owner, uint32 roundId, uint32 expiryBlock, bool revealed)
    {
        Commit storage c = commits[commitHash];
        return (c.owner, c.roundId, c.expiryBlock, c.revealed);
    }

    function getDomainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }
}
