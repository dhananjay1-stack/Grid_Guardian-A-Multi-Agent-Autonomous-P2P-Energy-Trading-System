# Grid-Guardian — Blockchain Layer

End-to-end implementation for registering Raspberry Pi edge nodes on an Ethereum-compatible L2 chain with EIP-712 meta-transactions, device attestation, off-chain KYC registry, **and collateral-based gas reimbursement via EIP-712 GasVouchers**.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    Raspberry Pi (Edge Node)                       │
│  ┌─────────────────┐  ┌───────────────────────────────────────┐  │
│  │ Secure Element   │  │ pi-client/                            │  │
│  │ ATECC608A / TPM  │  │  generate_keys.js    — keypair gen    │  │
│  │ (or SW keystore) │  │  register_via_relayer.js — EIP-712    │  │
│  └────────┬────────┘  │  device_attestation.js — HW attest    │  │
│           │           └───────────────┬───────────────────────┘  │
└───────────┼───────────────────────────┼──────────────────────────┘
            │                           │ HTTPS (signed payload)
            ▼                           ▼
┌─────────────────────────────────────────────────────┐
│              Relayer (relayer/server.js)             │
│  • Verifies EIP-712 signature                       │
│  • Submits registerNodeMeta() on-chain              │
│  • Proxies KYC to off-chain registry                │
│  • Pays gas on behalf of the Pi                     │
└───────────────────┬─────────────────────────────────┘
                    │ JSON-RPC
                    ▼
┌─────────────────────────────────────────────────────┐
│          L2 Chain (Hardhat / Anvil local)            │
│  ┌───────────────────────────────────────────────┐  │
│  │              IdentitySC.sol                    │  │
│  │  registerNode()       — direct (caller pays)  │  │
│  │  registerNodeMeta()   — meta-tx (relayer pays)│  │
│  │  revokeNode()         — admin revocation       │  │
│  │  attestNode()         — device attestation     │  │
│  │  updateMetaURI()      — owner updates CID      │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
┌──────────────┐    ┌──────────────────────┐
│   IPFS Node  │    │ Off-Chain Registry   │
│   (metaURI)  │    │ (Postgres + Express) │
│   CID stored │    │ PII / KYC stored     │
│   on-chain   │    │ here, NOT on-chain   │
└──────────────┘    └──────────────────────┘
```

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Node.js | v18+ LTS | Runtime |
| npm | v9+ | Package manager |
| Docker + Docker Compose | Latest | Postgres + IPFS containers |
| Git | Latest | Version control |
| VSCode | Latest | IDE + tasks/debugger |

### Recommended VSCode Extensions

- Solidity (NomicFoundation)
- ESLint + Prettier
- Docker
- Remote-SSH (for real Pi)

---

## Quick Start (step-by-step)

### 1. Install Dependencies

```bash
cd Blockchain
npm install

cd pi-client && npm install && cd ..
cd relayer   && npm install && cd ..
cd offchain-registry && npm install && cd ..
```

### 2. Start Infrastructure (Docker)

```bash
docker compose up -d
```

This starts:
- **PostgreSQL** on port 5432 (auto-creates `kyc_records` table)
- **IPFS (Kubo)** on ports 5001 (API) / 8080 (gateway)

### 3. Compile Smart Contracts

```bash
npx hardhat compile
```

### 4. Start Hardhat Local Chain

```bash
npx hardhat node
```

Leave this terminal running. It provides a local Ethereum RPC at `http://127.0.0.1:8545` with 20 pre-funded accounts.

### 5. Deploy IdentitySC

In a new terminal:

```bash
npx hardhat run scripts/deploy.js --network localhost
```

Copy the printed contract address into `.env`:
```
IDENTITY_SC_ADDRESS=0x5FbDB2315678afecb367f032d93F642f64180aa3
```

Also copy it into `pi-client/.env`.

### 6. Start Off-Chain Registry

```bash
npm run registry
# or: node offchain-registry/server.js
```

### 7. Start Relayer

```bash
npm run relayer
# or: node relayer/server.js
```

### 8. Pi: Generate Keys

```bash
cd pi-client
node generate_keys.js
```

Creates:
- `keystore/keystore.json` — encrypted wallet (mode 600)
- `keystore/device_salt.bin` — 32-byte random salt (mode 600)
- Prints: address, publicKey, nodeId, pubkeyHash

### 9. Pi: Register via Relayer

```bash
node register_via_relayer.js
```

This:
1. Loads the encrypted wallet
2. Computes nodeId from pubkey + salt
3. Creates a profile and stores KYC off-chain
4. Signs an EIP-712 message
5. POSTs to the relayer
6. Relayer submits the on-chain meta-tx

### 10. Pi: Device Attestation

```bash
node device_attestation.js
```

Sends a signed attestation blob to the relayer, which marks the node as attested on-chain.

---

## Step 2: Collateral & Gas Setup

### Architecture

```
      Pi Node                    Relayer                         L2 Chain
     ────────                   ─────────                    ──────────────
  deposit USDC ─────────────────────────────────────────→ CollateralSC.deposit()
  approveRelayer ───────────────────────────────────────→ CollateralSC.approveRelayer()
  request meta-tx ──→ /meta-tx/request ──→ execute tx ──→ target contract
                         │
                         │  (tx mined, gasUsed known)
                         │
                         ▼
  sign GasVoucher ────→ /voucher/submit ─→ claimGas() ──→ CollateralSC (USDC transfer)
  (EIP-712)               ← USDC to relayer ←
```

**Contracts:**
- **MockUSDC.sol** — ERC-20 stablecoin (6 decimals) for dev/test
- **CollateralSC.sol** — Manages deposits, relayer allowances, and EIP-712 GasVoucher claims

**Key Flows:**
1. Device owner deposits USDC into CollateralSC for their node
2. Device approves a relayer with a USDC allowance
3. Relayer executes meta-txs on behalf of the device
4. Device signs an EIP-712 GasVoucher authorizing reimbursement
5. Relayer submits the voucher to claim USDC from the device's deposit

### Deploy CollateralSC

```bash
# Set IDENTITY_SC_ADDRESS in .env first
npx hardhat run scripts/deploy_collateral.js --network localhost
```

Copy the printed addresses into `.env`:
```
MOCK_USDC_ADDRESS=0x...
COLLATERAL_SC_ADDRESS=0x...
```

### Pi Client: Gas Voucher Scripts

```bash
cd pi-client

# Sign a gas voucher after relayer executes a meta-tx
node sign_gas_voucher.js --txHash 0x... --gasUsed 50000 --gasPrice 1000000000 --relayer 0x...

# Request a meta-tx execution
node request_meta_tx.js
```

### Relayer Endpoints (Step 2)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/voucher/submit` | Submit signed GasVoucher for gas claim |
| `POST` | `/meta-tx/request` | Submit meta-tx for relayer execution |
| `GET`  | `/voucher/nonce/:nodeId` | Query voucher nonce |
| `GET`  | `/deposit/:nodeId` | Query deposit balance |
| `GET`  | `/allowance/:nodeId/:relayer` | Query relayer allowance |

---

## Running Tests

### Unit Tests (Hardhat)

```bash
npx hardhat test
```

Covers 14 identity test cases + 16 collateral test cases:

**IdentitySC (14 tests):**
1. Direct registration
2. Duplicate nodeId reverts
3. Meta-tx registration (EIP-712)
4. Invalid signature reverts
5. Expired signature reverts
6. Wrong nonce reverts
7. Admin revocation
8. Non-admin revocation reverts
9. Device attestation
10. MetaURI update by owner
11. Non-owner MetaURI update reverts
12. Duplicate owner reverts
13. isRegistered view helper
14. nodeCount tracking

**CollateralSC (16 tests):**
1. MockUSDC decimals + initial supply
2. Deposit increases balance
3. Deposit reverts for zero amount
4. Deposit reverts for unregistered node
5. approveRelayer only by owner
6. approveRelayer reverts for non-owner
7. claimGas full success flow
8. claimGas reverts on expired voucher
9. claimGas reverts for wrong relayer
10. claimGas reverts on invalid signature
11. claimGas reverts on insufficient allowance
12. claimGas reverts on insufficient deposit
13. claimGas reverts on replayed nonce
14. Admin withdraw
15. setMinDeposit admin
16. getDeposit + GasClaimed event

### Integration Tests

```bash
# Requires Hardhat node running

# Step 1: Identity flow
node scripts/integration/registerFlow.js

# Step 2: Gas flow (deposit → approve → claim)
node scripts/integration/gasFlow.js
```

---

## Project Structure

```
Blockchain/
├── contracts/
│   ├── IdentitySC.sol              # Node identity + EIP-712 meta-tx
│   ├── CollateralSC.sol            # Deposit, allowance, EIP-712 GasVoucher
│   └── MockUSDC.sol                # Dev/test ERC-20 stablecoin
├── scripts/
│   ├── deploy.js                   # Deploy IdentitySC
│   ├── deploy_collateral.js        # Deploy MockUSDC + CollateralSC
│   └── integration/
│       ├── registerFlow.js         # Step 1 integration test
│       └── gasFlow.js              # Step 2 integration test
├── pi-client/
│   ├── generate_keys.js            # Keypair + salt generation
│   ├── register_via_relayer.js     # EIP-712 registration
│   ├── device_attestation.js       # HW attestation (simulated)
│   ├── sign_gas_voucher.js         # Sign EIP-712 GasVoucher
│   ├── request_meta_tx.js          # Request meta-tx execution
│   └── keystore/                   # Generated keys (gitignored)
├── relayer/
│   └── server.js                   # Express relayer (identity + voucher)
├── offchain-registry/
│   ├── server.js                   # Express + Postgres KYC store
│   └── init.sql                    # DB schema
├── tests/
│   ├── identity.test.js            # Hardhat unit tests (14 cases)
│   └── collateral.test.js          # Hardhat unit tests (16 cases)
├── .vscode/
│   ├── tasks.json                  # All VSCode tasks
│   └── launch.json                 # Debug configurations
├── docker-compose.yml              # Postgres + IPFS
├── hardhat.config.js               # Hardhat configuration
├── package.json                    # Root dependencies
├── .env                            # Environment variables
└── .gitignore                      # Ignore node_modules, artifacts, keys
```

---

## Security Best Practices

| Practice | Implementation |
|----------|---------------|
| Private keys never in repo | `.gitignore` + POSIX 600 file perms |
| EIP-712 typed data signing | Replay-protected with nonces + expiry |
| PII off-chain only | Postgres stores KYC; only `metaURI` on-chain |
| Secure element preferred | ATECC608A/TPM path simulated; interfaces match production |
| Role-based access control | OpenZeppelin `AccessControl` with ADMIN + RELAYER roles |
| Reentrancy protection | `ReentrancyGuard` on registration functions |
| Device attestation | Signed HW serial + firmware hash verified before on-chain attestation |
| Gas-free for devices | Meta-tx relayer pays gas; Pi never needs native tokens |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RPC_URL` | `http://127.0.0.1:8545` | Hardhat/Anvil RPC endpoint |
| `PRIVATE_KEY` | Hardhat #0 | Deployer private key |
| `RELAYER_PRIVATE_KEY` | Hardhat #1 | Relayer private key |
| `IPFS_API` | `http://127.0.0.1:5001` | IPFS API endpoint |
| `POSTGRES_URL` | `postgres://postgres:password@...` | Postgres connection |
| `IDENTITY_SC_ADDRESS` | (set after deploy) | IdentitySC contract address |
| `MOCK_USDC_ADDRESS` | (set after deploy) | MockUSDC contract address |
| `COLLATERAL_SC_ADDRESS` | (set after deploy) | CollateralSC contract address |
| `KEYSTORE_PW` | `change_this_in_production` | Wallet encryption password |

---

## Verification Checklist

- [ ] Hardhat node running, IdentitySC deployed, address printed
- [ ] Postgres reachable, `kyc_records` table exists
- [ ] IPFS node up (port 5001), CID returned on profile upload
- [ ] Pi keystore created with mode 600
- [ ] `nodeId = keccak256(pubkey || salt)` matches relayer submission
- [ ] Relayer verifies EIP-712 signature and submits `registerNodeMeta`
- [ ] `NodeRegistered` event emitted on-chain
- [ ] `nodes[nodeId].owner == device address`
- [ ] Integration test passes end-to-end
- [ ] Admin can query KYC from off-chain registry
- [ ] Device attestation sets `attested = true` on-chain

### Step 2 — Collateral & Gas
- [ ] MockUSDC deployed, 6 decimals confirmed
- [ ] CollateralSC deployed, linked to IdentitySC + MockUSDC
- [ ] Device deposits USDC via CollateralSC
- [ ] Device approves relayer allowance
- [ ] GasVoucher EIP-712 signature verifies on-chain
- [ ] Relayer claims gas via `claimGas()`, USDC transferred
- [ ] Replay protection: same voucher rejected
- [ ] Collateral unit tests pass (16 cases)
- [ ] Gas flow integration test passes end-to-end

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Hardhat deploy fails | Check `RPC_URL` and `PRIVATE_KEY` in `.env` |
| Relayer rejects signature | Print domain/types/message on both sides; verify `verifyingContract` matches |
| IPFS upload fails | Ensure `docker compose up -d` ran and port 5001 is open |
| Postgres connection refused | Check Docker container health: `docker compose ps` |
| "node exists" revert | Each `nodeId` can only be registered once; use a new salt |
| "OwnerAlreadyRegistered" | Each address can own only one node |

---

## Optional Enhancements

- `registerNodeWithAttestation()` — verify attestation on-chain before registration
- Rate limiting on relayer (per-IP / per-address quotas)
- CI pipeline with GitHub Actions (Hardhat test on push)
- Upgrade to real IPFS pinning (Pinata / Web3.Storage) for metaURI
- Add Oracle for external data feeds (utility price, weather)
