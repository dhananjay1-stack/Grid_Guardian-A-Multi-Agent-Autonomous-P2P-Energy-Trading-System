// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title  IdentitySC — Grid-Guardian Node Onboarding & Identity Registry
 * @notice Registers edge nodes (Raspberry Pi) on-chain with:
 *         - keccak256(pubkey || salt) as nodeId
 *         - pubkeyHash + metaURI (IPFS CID pointing to encrypted profile)
 *         - EIP-712 meta-tx support so Pi can register without holding gas tokens
 *         - Device attestation verification (simulated in dev, HW-backed in prod)
 *         - Admin revocation with event trail
 */
contract IdentitySC is AccessControl, EIP712, ReentrancyGuard {
    using ECDSA for bytes32;

    // ── Roles ──────────────────────────────────────────────────────────
    bytes32 public constant ADMIN_ROLE   = keccak256("ADMIN_ROLE");
    bytes32 public constant RELAYER_ROLE = keccak256("RELAYER_ROLE");

    // ── EIP-712 typehash for meta-tx registration ──────────────────────
    bytes32 public constant REGISTER_TYPEHASH = keccak256(
        "RegisterNode(bytes32 nodeId,bytes32 pubkeyHash,string metaURI,uint256 nonce,uint256 expiry)"
    );

    // ── Storage ────────────────────────────────────────────────────────
    struct Node {
        address  owner;        // device's Ethereum address
        bytes32  pubkeyHash;   // keccak256(pubkey) — salted hash kept off-chain
        string   metaURI;      // IPFS CID or HTTPS link to encrypted off-chain profile
        uint256  stake;        // collateral deposit (future use)
        uint256  registeredAt; // block.timestamp of registration
        bool     active;       // revocable by admin
        bool     attested;     // device attestation verified
    }

    mapping(bytes32 => Node)    public nodes;        // nodeId → Node
    mapping(address => bytes32) public ownerToNode;   // owner address → nodeId (1:1)
    mapping(address => uint256) public nonces;        // per-address replay nonce

    uint256 public nodeCount;

    // ── Events ─────────────────────────────────────────────────────────
    event NodeRegistered(
        bytes32 indexed nodeId,
        address indexed owner,
        string  metaURI,
        uint256 timestamp
    );
    event NodeRevoked(bytes32 indexed nodeId, address indexed revokedBy);
    event NodeAttested(bytes32 indexed nodeId, address indexed attestedBy);
    event MetaURIUpdated(bytes32 indexed nodeId, string newMetaURI);

    // ── Errors ─────────────────────────────────────────────────────────
    error NodeAlreadyExists(bytes32 nodeId);
    error NodeDoesNotExist(bytes32 nodeId);
    error OwnerAlreadyRegistered(address owner);
    error InvalidSignature();
    error SignatureExpired();
    error InvalidNonce();
    error NotNodeOwner();

    // ── Constructor ────────────────────────────────────────────────────
    constructor(address admin)
        EIP712("GridGuardian-Relayer", "1")
    {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ADMIN_ROLE, admin);
    }

    // ════════════════════════════════════════════════════════════════════
    //                      DIRECT REGISTRATION
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Register a node directly (caller pays gas).
     * @param nodeId     keccak256(pubkey || salt) computed off-device
     * @param pubkeyHash keccak256(pubkey)
     * @param metaURI    IPFS CID or HTTPS link with encrypted profile
     */
    function registerNode(
        bytes32 nodeId,
        bytes32 pubkeyHash,
        string  calldata metaURI
    ) external nonReentrant {
        _register(nodeId, pubkeyHash, metaURI, msg.sender);
    }

    // ════════════════════════════════════════════════════════════════════
    //              META-TX REGISTRATION (relayer pays gas)
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Register on behalf of a device using an EIP-712 signature.
     *         The relayer submits the tx and pays gas; the device's address
     *         becomes the node owner.
     * @param nodeId      keccak256(pubkey || salt)
     * @param pubkeyHash  keccak256(pubkey)
     * @param metaURI     IPFS CID / HTTPS
     * @param nonce       Per-device replay nonce
     * @param expiry      Unix timestamp after which the signature is invalid
     * @param signature   EIP-712 signature from the device wallet
     */
    function registerNodeMeta(
        bytes32 nodeId,
        bytes32 pubkeyHash,
        string  calldata metaURI,
        uint256 nonce,
        uint256 expiry,
        bytes   calldata signature
    ) external nonReentrant onlyRole(RELAYER_ROLE) {
        // ── Verify expiry ──
        if (block.timestamp > expiry) revert SignatureExpired();

        // ── Build EIP-712 struct hash ──
        bytes32 structHash = keccak256(
            abi.encode(
                REGISTER_TYPEHASH,
                nodeId,
                pubkeyHash,
                keccak256(bytes(metaURI)),
                nonce,
                expiry
            )
        );
        bytes32 digest = _hashTypedDataV4(structHash);

        // ── Recover signer ──
        address signer = ECDSA.recover(digest, signature);
        if (signer == address(0)) revert InvalidSignature();

        // ── Verify nonce ──
        if (nonces[signer] != nonce) revert InvalidNonce();
        nonces[signer]++;

        // ── Register with signer as owner ──
        _register(nodeId, pubkeyHash, metaURI, signer);
    }

    // ════════════════════════════════════════════════════════════════════
    //                     ADMIN / LIFECYCLE
    // ════════════════════════════════════════════════════════════════════

    /**
     * @notice Revoke a node (admin only).
     */
    function revokeNode(bytes32 nodeId) external onlyRole(ADMIN_ROLE) {
        if (nodes[nodeId].registeredAt == 0) revert NodeDoesNotExist(nodeId);
        nodes[nodeId].active = false;
        emit NodeRevoked(nodeId, msg.sender);
    }

    /**
     * @notice Mark a node as attested (admin / relayer verifies attestation off-chain).
     */
    function attestNode(bytes32 nodeId) external onlyRole(ADMIN_ROLE) {
        if (nodes[nodeId].registeredAt == 0) revert NodeDoesNotExist(nodeId);
        nodes[nodeId].attested = true;
        emit NodeAttested(nodeId, msg.sender);
    }

    /**
     * @notice Owner can update their metaURI (e.g. new IPFS CID after KYC update).
     */
    function updateMetaURI(bytes32 nodeId, string calldata newMetaURI) external {
        if (nodes[nodeId].owner != msg.sender) revert NotNodeOwner();
        nodes[nodeId].metaURI = newMetaURI;
        emit MetaURIUpdated(nodeId, newMetaURI);
    }

    // ── View helpers ───────────────────────────────────────────────────

    function isRegistered(bytes32 nodeId) external view returns (bool) {
        return nodes[nodeId].registeredAt != 0;
    }

    function isActive(bytes32 nodeId) external view returns (bool) {
        return nodes[nodeId].active;
    }

    function getNode(bytes32 nodeId)
        external view
        returns (
            address  owner,
            bytes32  pubkeyHash,
            string memory metaURI,
            uint256  stake,
            uint256  registeredAt,
            bool     active,
            bool     attested
        )
    {
        Node storage n = nodes[nodeId];
        return (n.owner, n.pubkeyHash, n.metaURI, n.stake, n.registeredAt, n.active, n.attested);
    }

    /**
     * @notice Returns the EIP-712 domain separator (useful for Pi client).
     */
    function getDomainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }

    // ── Internal ───────────────────────────────────────────────────────

    function _register(
        bytes32 nodeId,
        bytes32 pubkeyHash,
        string  calldata metaURI,
        address owner
    ) internal {
        if (nodes[nodeId].registeredAt != 0) revert NodeAlreadyExists(nodeId);
        if (ownerToNode[owner] != bytes32(0))  revert OwnerAlreadyRegistered(owner);

        nodes[nodeId] = Node({
            owner:        owner,
            pubkeyHash:   pubkeyHash,
            metaURI:      metaURI,
            stake:        0,
            registeredAt: block.timestamp,
            active:       true,
            attested:     false
        });
        ownerToNode[owner] = nodeId;
        nodeCount++;

        emit NodeRegistered(nodeId, owner, metaURI, block.timestamp);
    }
}
