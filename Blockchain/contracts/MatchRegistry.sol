// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title  MatchRegistry — Grid-Guardian Optimizer Trust & Disputability
 * @notice Accepts signed MatchResult blobs from optimizers, stores matches,
 *         enforces a challenge window, and coordinates settlement.
 *
 * Flow:
 *   1. Optimizer deposits a bond (USDC) via depositOptimizerBond().
 *   2. Optimizer collects m-of-n EIP-712 signatures from operators.
 *   3. Optimizer calls publishMatch() with matchHash, inputsHash, signatures.
 *   4. Challenge window opens (default 60 blocks).
 *   5. Anyone can challengeMatch() within the window by posting a challengeBond.
 *   6. Off-chain adjudicator resolves: resolveChallenge() slashes or rejects.
 *   7. After window closes with no valid challenge, finalizeMatch() settles.
 *
 * Security:
 *   - EIP-712 typed signatures for MatchResult (replay-protected via roundId + nonce).
 *   - m-of-n operator signatures required (configurable quorum).
 *   - Optimizer bond slashed on proven misbehavior.
 *   - Challenge bond deters frivolous challenges; refunded + rewarded on valid challenge.
 *   - SafeERC20 for all token transfers.
 */
contract MatchRegistry is AccessControl, EIP712, ReentrancyGuard {
    using ECDSA for bytes32;
    using SafeERC20 for IERC20;

    // ── Roles ──────────────────────────────────────────────────────────
    bytes32 public constant ADMIN_ROLE       = keccak256("ADMIN_ROLE");
    bytes32 public constant ADJUDICATOR_ROLE = keccak256("ADJUDICATOR_ROLE");

    // ── EIP-712 MatchResult typehash ───────────────────────────────────
    bytes32 public constant MATCHRESULT_TYPEHASH = keccak256(
        "MatchResult(uint32 roundId,bytes32 matchHash,bytes32 inputsHash,uint256 publishNonce,uint256 timestamp)"
    );

    // ── External references ────────────────────────────────────────────
    IERC20 public immutable stablecoin;

    // ── Configuration ──────────────────────────────────────────────────
    uint256 public challengeWindowBlocks;  // blocks to wait for challenges
    uint256 public matchPublishBond;       // required optimizer bond
    uint256 public challengeBond;          // required challenger bond (USDC)
    uint256 public requiredSigners;        // m in m-of-n quorum

    // ── Operator registry (n signers) ──────────────────────────────────
    mapping(address => bool) public operators;
    uint256 public operatorCount;

    // ── Storage ────────────────────────────────────────────────────────
    enum MatchStatus { None, Published, Challenged, Finalized, Invalidated }

    struct MatchMeta {
        address   publisher;        // who submitted the match
        bytes32   inputsHash;       // canonical hash of commit inputs
        uint256   publishedBlock;   // block when published
        uint32    roundId;          // market round
        uint256   publishNonce;     // monotonic per optimizer
        MatchStatus status;         // lifecycle state
        uint8     validSigners;     // count of valid operator signatures
    }

    struct Challenge {
        address   challenger;       // who challenged
        bytes32   altMatchHash;     // proposed alternative match hash
        uint256   challengeBlock;   // block when challenged
        uint256   bondAmount;       // USDC escrowed
        bool      resolved;         // whether adjudicator resolved this
    }

    // matchHash => MatchMeta
    mapping(bytes32 => MatchMeta) public matches;

    // matchHash => Challenge
    mapping(bytes32 => Challenge) public challenges;

    // optimizerId => bonded amount
    mapping(bytes32 => uint256) public bonds;

    // optimizerId => nonce (monotonic per optimizer for replay protection)
    mapping(bytes32 => uint256) public optimizerNonce;

    // ── Events ─────────────────────────────────────────────────────────
    event MatchPublished(
        bytes32 indexed matchHash,
        bytes32 inputsHash,
        address indexed publisher,
        uint32 roundId,
        uint256 publishedBlock
    );
    event MatchChallenged(
        bytes32 indexed matchHash,
        bytes32 indexed altMatchHash,
        address indexed challenger,
        uint256 bondAmount
    );
    event ChallengeResolved(
        bytes32 indexed matchHash,
        bool challengeValid,
        address indexed adjudicator
    );
    event MatchFinalized(bytes32 indexed matchHash, address indexed executor);
    event MatchInvalidated(bytes32 indexed matchHash);
    event OptimizerBondDeposited(bytes32 indexed optimizerId, address indexed depositor, uint256 amount);
    event OptimizerSlashed(bytes32 indexed optimizerId, address indexed to, uint256 amount);
    event OperatorAdded(address indexed operator);
    event OperatorRemoved(address indexed operator);
    event ConfigUpdated(string param, uint256 newValue);

    // ── Errors ─────────────────────────────────────────────────────────
    error MatchAlreadyPublished();
    error InsufficientOptimizerBond();
    error InsufficientSigners();
    error NoSuchMatch();
    error ChallengeWindowClosed();
    error ChallengeWindowOpen();
    error MatchNotPublished();
    error MatchAlreadyChallenged();
    error AlreadyFinalized();
    error AlreadyInvalidated();
    error MatchBlobMismatch();
    error ChallengeAlreadyResolved();
    error InsufficientBondForSlash();
    error ZeroAmount();

    // ── Constructor ────────────────────────────────────────────────────
    constructor(
        address _stablecoin,
        address admin,
        uint256 _challengeWindow,
        uint256 _matchBond,
        uint256 _challengeBond,
        uint256 _requiredSigners
    ) EIP712("GridGuardian-Match", "1") {
        stablecoin = IERC20(_stablecoin);
        challengeWindowBlocks = _challengeWindow;
        matchPublishBond = _matchBond;
        challengeBond = _challengeBond;
        requiredSigners = _requiredSigners;

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ADMIN_ROLE, admin);
        _grantRole(ADJUDICATOR_ROLE, admin);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     OPERATOR MANAGEMENT
    // ════════════════════════════════════════════════════════════════════

    function addOperator(address op) external onlyRole(ADMIN_ROLE) {
        require(!operators[op], "already operator");
        operators[op] = true;
        operatorCount++;
        emit OperatorAdded(op);
    }

    function removeOperator(address op) external onlyRole(ADMIN_ROLE) {
        require(operators[op], "not operator");
        operators[op] = false;
        operatorCount--;
        emit OperatorRemoved(op);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     OPTIMIZER BOND
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Deposit USDC bond for an optimizer.
     * @param optimizerId Identifier for the optimizer
     * @param amount      USDC amount to deposit (caller must approve first)
     */
    function depositOptimizerBond(bytes32 optimizerId, uint256 amount) external nonReentrant {
        if (amount == 0) revert ZeroAmount();
        stablecoin.safeTransferFrom(msg.sender, address(this), amount);
        bonds[optimizerId] += amount;
        emit OptimizerBondDeposited(optimizerId, msg.sender, amount);
    }

    /**
     * @notice Slash an optimizer's bond (admin/adjudicator only).
     * @param optimizerId The optimizer to slash
     * @param to          Recipient of slashed funds (challenger or treasury)
     * @param amount      Amount to slash
     */
    function slashOptimizer(
        bytes32 optimizerId,
        address to,
        uint256 amount
    ) external onlyRole(ADJUDICATOR_ROLE) nonReentrant {
        if (bonds[optimizerId] < amount) revert InsufficientBondForSlash();
        bonds[optimizerId] -= amount;
        stablecoin.safeTransfer(to, amount);
        emit OptimizerSlashed(optimizerId, to, amount);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     PUBLISH MATCH
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Optimizer publishes a match result on-chain.
     * @param matchHash    keccak256(matchBlob) — stored off-chain
     * @param inputsHash   Canonical hash of sorted commit inputs
     * @param roundId      Market round this match belongs to
     * @param optimizerId  Optimizer's bond identifier
     * @param signatures   Concatenated EIP-712 signatures from operators (65 bytes each)
     */
    function publishMatch(
        bytes32 matchHash,
        bytes32 inputsHash,
        uint32  roundId,
        bytes32 optimizerId,
        uint256 sigTimestamp,
        bytes calldata signatures
    ) external nonReentrant {
        if (matches[matchHash].publishedBlock != 0) revert MatchAlreadyPublished();
        if (bonds[optimizerId] < matchPublishBond) revert InsufficientOptimizerBond();

        // ── Verify m-of-n operator signatures ──
        uint256 nonce = optimizerNonce[optimizerId];
        uint256 validCount = _verifyOperatorSignatures(
            matchHash, inputsHash, roundId, nonce, sigTimestamp, signatures
        );
        if (validCount < requiredSigners) revert InsufficientSigners();

        // ── Increment nonce ──
        optimizerNonce[optimizerId] = nonce + 1;

        // ── Store match metadata ──
        matches[matchHash] = MatchMeta({
            publisher:      msg.sender,
            inputsHash:     inputsHash,
            publishedBlock: block.number,
            roundId:        roundId,
            publishNonce:   nonce,
            status:         MatchStatus.Published,
            validSigners:   uint8(validCount)
        });

        emit MatchPublished(matchHash, inputsHash, msg.sender, roundId, block.number);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     CHALLENGE MATCH
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Challenge a published match within the challenge window.
     *         Challenger must approve challengeBond USDC to this contract.
     * @param matchHash    The published match to challenge
     * @param altMatchHash Proposed alternative match hash
     */
    function challengeMatch(
        bytes32 matchHash,
        bytes32 altMatchHash
    ) external nonReentrant {
        MatchMeta storage m = matches[matchHash];
        if (m.publishedBlock == 0) revert NoSuchMatch();
        if (m.status != MatchStatus.Published) revert MatchNotPublished();
        if (block.number > m.publishedBlock + challengeWindowBlocks) revert ChallengeWindowClosed();
        if (challenges[matchHash].challenger != address(0)) revert MatchAlreadyChallenged();

        // ── Escrow challenge bond ──
        stablecoin.safeTransferFrom(msg.sender, address(this), challengeBond);

        challenges[matchHash] = Challenge({
            challenger:     msg.sender,
            altMatchHash:   altMatchHash,
            challengeBlock: block.number,
            bondAmount:     challengeBond,
            resolved:       false
        });

        m.status = MatchStatus.Challenged;

        emit MatchChallenged(matchHash, altMatchHash, msg.sender, challengeBond);
    }

    // ════════════════════════════════════════════════════════════════════
    //                    RESOLVE CHALLENGE (ADJUDICATOR)
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Adjudicator resolves a challenge.
     * @param matchHash      The challenged match
     * @param challengeValid True if challenger was right (optimizer slashed),
     *                       false if frivolous (challenger bond forfeited)
     * @param optimizerId    Optimizer to slash if challenge is valid
     * @param slashAmount    Amount to slash from optimizer bond (if valid)
     */
    function resolveChallenge(
        bytes32 matchHash,
        bool    challengeValid,
        bytes32 optimizerId,
        uint256 slashAmount
    ) external onlyRole(ADJUDICATOR_ROLE) nonReentrant {
        MatchMeta storage m = matches[matchHash];
        if (m.status != MatchStatus.Challenged) revert MatchNotPublished();

        Challenge storage ch = challenges[matchHash];
        if (ch.resolved) revert ChallengeAlreadyResolved();
        ch.resolved = true;

        if (challengeValid) {
            // ── Challenge accepted: slash optimizer, reward challenger ──
            m.status = MatchStatus.Invalidated;

            // Slash optimizer bond
            if (bonds[optimizerId] >= slashAmount) {
                bonds[optimizerId] -= slashAmount;
                stablecoin.safeTransfer(ch.challenger, slashAmount);
                emit OptimizerSlashed(optimizerId, ch.challenger, slashAmount);
            }

            // Refund challenge bond to challenger
            stablecoin.safeTransfer(ch.challenger, ch.bondAmount);

            emit MatchInvalidated(matchHash);
        } else {
            // ── Frivolous challenge: forfeit challenger bond to publisher ──
            m.status = MatchStatus.Published; // revert to published so it can be finalized
            stablecoin.safeTransfer(m.publisher, ch.bondAmount);
        }

        emit ChallengeResolved(matchHash, challengeValid, msg.sender);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     FINALIZE MATCH
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Finalize a match after the challenge window has passed.
     *         Verifies keccak256(matchBlob) == matchHash.
     * @param matchHash The match to finalize
     * @param matchBlob The full match blob (verified against matchHash)
     */
    function finalizeMatch(
        bytes32 matchHash,
        bytes calldata matchBlob
    ) external nonReentrant {
        MatchMeta storage m = matches[matchHash];
        if (m.publishedBlock == 0) revert NoSuchMatch();
        if (m.status == MatchStatus.Finalized) revert AlreadyFinalized();
        if (m.status == MatchStatus.Invalidated) revert AlreadyInvalidated();
        if (m.status == MatchStatus.Challenged) revert ChallengeWindowOpen(); // still in dispute
        if (block.number <= m.publishedBlock + challengeWindowBlocks) revert ChallengeWindowOpen();

        // ── Verify blob integrity ──
        if (keccak256(matchBlob) != matchHash) revert MatchBlobMismatch();

        m.status = MatchStatus.Finalized;

        emit MatchFinalized(matchHash, msg.sender);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     ADMIN CONFIGURATION
    // ════════════════════════════════════════════════════════════════════

    function setChallengeWindow(uint256 blocks) external onlyRole(ADMIN_ROLE) {
        challengeWindowBlocks = blocks;
        emit ConfigUpdated("challengeWindowBlocks", blocks);
    }

    function setMatchPublishBond(uint256 amount) external onlyRole(ADMIN_ROLE) {
        matchPublishBond = amount;
        emit ConfigUpdated("matchPublishBond", amount);
    }

    function setChallengeBond(uint256 amount) external onlyRole(ADMIN_ROLE) {
        challengeBond = amount;
        emit ConfigUpdated("challengeBond", amount);
    }

    function setRequiredSigners(uint256 count) external onlyRole(ADMIN_ROLE) {
        requiredSigners = count;
        emit ConfigUpdated("requiredSigners", count);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     VIEW HELPERS
    // ════════════════════════════════════════════════════════════════════

    function getMatch(bytes32 matchHash)
        external view
        returns (
            address publisher,
            bytes32 inputsHash,
            uint256 publishedBlock,
            uint32  roundId,
            uint256 publishNonce,
            MatchStatus status,
            uint8   validSigners
        )
    {
        MatchMeta storage m = matches[matchHash];
        return (m.publisher, m.inputsHash, m.publishedBlock, m.roundId, m.publishNonce, m.status, m.validSigners);
    }

    function getChallenge(bytes32 matchHash)
        external view
        returns (
            address challenger,
            bytes32 altMatchHash,
            uint256 challengeBlock,
            uint256 bondAmount,
            bool    resolved
        )
    {
        Challenge storage ch = challenges[matchHash];
        return (ch.challenger, ch.altMatchHash, ch.challengeBlock, ch.bondAmount, ch.resolved);
    }

    function getOptimizerBond(bytes32 optimizerId) external view returns (uint256) {
        return bonds[optimizerId];
    }

    function getDomainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }

    // ════════════════════════════════════════════════════════════════════
    //                     INTERNAL: SIGNATURE VERIFICATION
    // ════════════════════════════════════════════════════════════════════

    /**
     * @dev Verify m-of-n operator EIP-712 signatures on a MatchResult.
     *      Each signature is 65 bytes (r, s, v) concatenated.
     *      Duplicate signers are ignored.
     */
    function _verifyOperatorSignatures(
        bytes32 matchHash,
        bytes32 inputsHash,
        uint32  roundId,
        uint256 publishNonce,
        uint256 sigTimestamp,
        bytes calldata signatures
    ) internal view returns (uint256 validCount) {
        uint256 sigCount = signatures.length / 65;
        if (sigCount == 0) return 0;

        // Build EIP-712 digest using the caller-provided timestamp
        // (operators sign this timestamp off-chain before the tx is mined)
        bytes32 structHash = keccak256(abi.encode(
            MATCHRESULT_TYPEHASH,
            roundId,
            matchHash,
            inputsHash,
            publishNonce,
            sigTimestamp
        ));
        bytes32 digest = _hashTypedDataV4(structHash);

        // Track seen signers to prevent duplicates
        address[] memory seen = new address[](sigCount);

        for (uint256 i = 0; i < sigCount; i++) {
            bytes calldata sig = signatures[i * 65:(i + 1) * 65];
            address signer = ECDSA.recover(digest, sig);

            if (operators[signer] && !_contains(seen, signer, validCount)) {
                seen[validCount] = signer;
                validCount++;
            }
        }
    }

    function _contains(address[] memory arr, address val, uint256 len) internal pure returns (bool) {
        for (uint256 i = 0; i < len; i++) {
            if (arr[i] == val) return true;
        }
        return false;
    }
}
