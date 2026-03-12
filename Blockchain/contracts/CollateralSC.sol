// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title  CollateralSC — Grid-Guardian Collateral & Gas Reimbursement
 * @notice Manages node deposits (stablecoin), relayer allowances, and
 *         EIP-712 signed GasVoucher claims for relayer gas reimbursement.
 *
 * Flow:
 *   1. Node deposits MockUSDC via deposit(nodeId, amount).
 *   2. Node owner calls approveRelayer(nodeId, relayer, allowance).
 *   3. Pi signs a GasVoucher after relayer executes a meta-tx.
 *   4. Relayer calls claimGas(voucher, signature) to get reimbursed.
 *
 * Security:
 *   - Voucher nonce prevents replay.
 *   - Expiry prevents stale vouchers.
 *   - txHash links voucher to the specific transaction.
 *   - Only the registered node owner (from IdentitySC) can sign vouchers.
 *   - Allowance caps per-relayer spending.
 */

interface IIdentitySC {
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

contract CollateralSC is EIP712, AccessControl, ReentrancyGuard {
    using ECDSA for bytes32;
    using SafeERC20 for IERC20;

    // ── Roles ──────────────────────────────────────────────────────────
    bytes32 public constant ADMIN_ROLE = keccak256("ADMIN_ROLE");

    // ── External references ────────────────────────────────────────────
    IERC20 public immutable stablecoin;
    IIdentitySC public immutable identitySC;

    // ── Storage ────────────────────────────────────────────────────────
    mapping(bytes32 => uint256) public deposits;                          // nodeId => balance
    mapping(bytes32 => mapping(address => uint256)) public relayerAllowance; // nodeId => relayer => allowance
    mapping(bytes32 => uint256) public voucherNonce;                      // nodeId => nonce

    uint256 public minDeposit;  // admin-configurable minimum deposit

    // ── EIP-712 GasVoucher type ────────────────────────────────────────
    struct GasVoucher {
        bytes32 nodeId;
        address relayer;
        uint256 amount;      // stablecoin reimbursement amount
        uint256 maxGas;      // gas cap
        uint256 gasPrice;    // gas price used
        uint256 nonce;       // replay-protection nonce
        uint256 expiry;      // block.timestamp deadline
        bytes32 txHash;      // hash of the executed tx (links voucher to tx)
    }

    bytes32 public constant GASVOUCHER_TYPEHASH = keccak256(
        "GasVoucher(bytes32 nodeId,address relayer,uint256 amount,uint256 maxGas,uint256 gasPrice,uint256 nonce,uint256 expiry,bytes32 txHash)"
    );

    // ── Events ─────────────────────────────────────────────────────────
    event Deposited(bytes32 indexed nodeId, address indexed from, uint256 amount);
    event Withdrawn(bytes32 indexed nodeId, address indexed to, uint256 amount);
    event RelayerApproved(bytes32 indexed nodeId, address indexed relayer, uint256 allowance);
    event GasClaimed(bytes32 indexed nodeId, address indexed relayer, uint256 amount, uint256 nonce);
    event MinDepositUpdated(uint256 oldMin, uint256 newMin);

    // ── Errors ─────────────────────────────────────────────────────────
    error ZeroAmount();
    error InsufficientDeposit();
    error NotNodeOwner();
    error NodeNotActive();
    error InvalidRelayer();
    error VoucherExpired();
    error InvalidVoucherNonce();
    error AllowanceExceeded();
    error InvalidVoucherSignature();
    error BelowMinDeposit();

    // ── Constructor ────────────────────────────────────────────────────
    constructor(
        address _stablecoin,
        address _identitySC,
        address admin
    ) EIP712("GridGuardian-Collateral", "1") {
        stablecoin = IERC20(_stablecoin);
        identitySC = IIdentitySC(_identitySC);
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ADMIN_ROLE, admin);
    }

    // ════════════════════════════════════════════════════════════════════
    //                         DEPOSITS
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Deposit stablecoin into a node's collateral account.
     *         Caller must have approved this contract for `amount`.
     * @param nodeId The registered node ID from IdentitySC
     * @param amount Amount of stablecoin to deposit
     */
    function deposit(bytes32 nodeId, uint256 amount) external nonReentrant {
        if (amount == 0) revert ZeroAmount();
        // Verify node exists and is active
        (, , , , uint256 registeredAt, bool active, ) = identitySC.nodes(nodeId);
        require(registeredAt != 0, "node not registered");
        require(active, "node not active");

        stablecoin.safeTransferFrom(msg.sender, address(this), amount);
        deposits[nodeId] += amount;
        emit Deposited(nodeId, msg.sender, amount);
    }

    /**
     * @notice Admin withdrawal (emergency or admin-managed refund).
     * @param nodeId Node whose deposit to withdraw from
     * @param amount Amount to withdraw
     * @param to     Recipient address
     */
    function withdraw(bytes32 nodeId, uint256 amount, address to)
        external
        onlyRole(ADMIN_ROLE)
        nonReentrant
    {
        if (deposits[nodeId] < amount) revert InsufficientDeposit();
        deposits[nodeId] -= amount;
        stablecoin.safeTransfer(to, amount);
        emit Withdrawn(nodeId, to, amount);
    }

    // ════════════════════════════════════════════════════════════════════
    //                    RELAYER ALLOWANCE
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Node owner sets the stablecoin allowance a relayer can claim.
     * @param nodeId    The node this allowance is for
     * @param relayer   The relayer address authorized to claim gas
     * @param allowance Maximum stablecoin the relayer can claim (cumulative)
     */
    function approveRelayer(
        bytes32 nodeId,
        address relayer,
        uint256 allowance
    ) external {
        (address owner, , , , , bool active, ) = identitySC.nodes(nodeId);
        if (owner != msg.sender) revert NotNodeOwner();
        if (!active) revert NodeNotActive();
        relayerAllowance[nodeId][relayer] = allowance;
        emit RelayerApproved(nodeId, relayer, allowance);
    }

    // ════════════════════════════════════════════════════════════════════
    //                  GAS VOUCHER CLAIM (relayer calls)
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Relayer presents a Pi-signed GasVoucher to claim reimbursement.
     * @param voucher   The GasVoucher struct with claim details
     * @param signature EIP-712 signature from the node owner
     */
    function claimGas(
        GasVoucher calldata voucher,
        bytes calldata signature
    ) external nonReentrant {
        // ── 1. Check expiry ──
        if (block.timestamp > voucher.expiry) revert VoucherExpired();

        // ── 2. Relayer must be the caller ──
        if (voucher.relayer != msg.sender) revert InvalidRelayer();

        // ── 3. Verify nonce ──
        if (voucher.nonce != voucherNonce[voucher.nodeId]) revert InvalidVoucherNonce();

        // ── 4. Check allowance ──
        if (relayerAllowance[voucher.nodeId][msg.sender] < voucher.amount)
            revert AllowanceExceeded();

        // ── 5. Check deposit balance ──
        if (deposits[voucher.nodeId] < voucher.amount) revert InsufficientDeposit();

        // ── 6. Verify EIP-712 signature from node owner ──
        bytes32 structHash = keccak256(abi.encode(
            GASVOUCHER_TYPEHASH,
            voucher.nodeId,
            voucher.relayer,
            voucher.amount,
            voucher.maxGas,
            voucher.gasPrice,
            voucher.nonce,
            voucher.expiry,
            voucher.txHash
        ));
        bytes32 digest = _hashTypedDataV4(structHash);
        address signer = ECDSA.recover(digest, signature);

        (address owner, , , , , bool active, ) = identitySC.nodes(voucher.nodeId);
        if (!active) revert NodeNotActive();
        if (signer != owner) revert InvalidVoucherSignature();

        // ── 7. Update state ──
        voucherNonce[voucher.nodeId] += 1;
        relayerAllowance[voucher.nodeId][msg.sender] -= voucher.amount;
        deposits[voucher.nodeId] -= voucher.amount;
        stablecoin.safeTransfer(msg.sender, voucher.amount);

        emit GasClaimed(voucher.nodeId, msg.sender, voucher.amount, voucher.nonce);
    }

    // ════════════════════════════════════════════════════════════════════
    //                        ADMIN
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Set the minimum deposit threshold.
     */
    function setMinDeposit(uint256 newMin) external onlyRole(ADMIN_ROLE) {
        uint256 old = minDeposit;
        minDeposit = newMin;
        emit MinDepositUpdated(old, newMin);
    }

    // ── View helpers ───────────────────────────────────────────────────

    function getDeposit(bytes32 nodeId) external view returns (uint256) {
        return deposits[nodeId];
    }

    function getAllowance(bytes32 nodeId, address relayer) external view returns (uint256) {
        return relayerAllowance[nodeId][relayer];
    }

    function getVoucherNonce(bytes32 nodeId) external view returns (uint256) {
        return voucherNonce[nodeId];
    }

    function getDomainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }
}
