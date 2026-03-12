/**
 * scripts/integration/simulate_pi_commit_reveal.js
 *
 * End-to-end integration test for Step 3: Local Sensing → Offer Creation
 *
 * Flow:
 *   1. Connect to running Hardhat node.
 *   2. Deploy IdentitySC + TradingSC.
 *   3. Register a device node in IdentitySC.
 *   4. Pi composes a quantized offer (compose_offer.js).
 *   5. Pi signs the offer via EIP-712 (sign_offer.js).
 *   6. Compute commitHash and post on-chain (publish_commit_onchain.js).
 *   7. Broadcast offer off-chain P2P (broadcast_offer_p2p.js).
 *   8. Reveal the offer on-chain (reveal_offer.js).
 *   9. Verify all states: commit stored, revealed = true, events emitted.
 *
 * Prerequisites:
 *   - Hardhat node running on :8545
 *   - Contracts compiled (`npx hardhat compile`)
 *
 * Usage:
 *   node scripts/integration/simulate_pi_commit_reveal.js
 */
require("dotenv").config();
const { ethers } = require("ethers");
const crypto = require("crypto");
const path   = require("path");
const fs     = require("fs");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL = process.env.RPC_URL || "http://127.0.0.1:8545";

// ── Load contract artifacts ────────────────────────────────────────
function loadArtifact(name) {
  const p = path.resolve(__dirname, `../../artifacts/contracts/${name}.sol/${name}.json`);
  if (!fs.existsSync(p)) {
    console.error(`❌  Artifact not found: ${name}. Run \`npx hardhat compile\` first.`);
    process.exit(1);
  }
  const a = JSON.parse(fs.readFileSync(p, "utf-8"));
  return { abi: a.abi, bytecode: a.bytecode };
}

// ── Offer helpers (same logic as pi-client scripts) ────────────────
function composeOfferStructHash(roundId, nodeId, kwhBucket, priceBucket, nonce, expiryBlock) {
  const OFFER_TYPEHASH = ethers.keccak256(
    ethers.toUtf8Bytes(
      "Offer(uint32 roundId,bytes32 nodeId,uint16 kwhBucket,uint16 priceBucket,uint32 nonce,uint32 expiryBlock)"
    )
  );
  return ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ["bytes32", "uint32", "bytes32", "uint16", "uint16", "uint32", "uint32"],
      [OFFER_TYPEHASH, roundId, nodeId, kwhBucket, priceBucket, nonce, expiryBlock]
    )
  );
}

function computeCommitHash(offerHash, salt) {
  return ethers.keccak256(
    ethers.solidityPacked(["bytes32", "bytes32"], [offerHash, salt])
  );
}

// ── Main ───────────────────────────────────────────────────────────
async function main() {
  console.log("╔══════════════════════════════════════════════════════════════╗");
  console.log("║   Step 3: Commit-Reveal Integration Test                    ║");
  console.log("╚══════════════════════════════════════════════════════════════╝\n");

  const provider = new ethers.JsonRpcProvider(RPC_URL);

  // Use Hardhat accounts via JsonRpcSigner (avoids nonce caching issues)
  const deployer = await provider.getSigner(0);
  const piDevice = await provider.getSigner(2); // use account #2 as Pi device
  const deployerAddr = await deployer.getAddress();
  const piAddr = await piDevice.getAddress();

  console.log(`  Deployer : ${deployerAddr}`);
  console.log(`  Pi device: ${piAddr}\n`);

  // ── 1. Deploy IdentitySC ──
  console.log("1️⃣  Deploying IdentitySC...");
  const identityArt = loadArtifact("IdentitySC");
  const IdentityFactory = new ethers.ContractFactory(identityArt.abi, identityArt.bytecode, deployer);
  const identitySC = await IdentityFactory.deploy(deployerAddr);
  await identitySC.waitForDeployment();
  const identityAddr = await identitySC.getAddress();
  console.log(`   IdentitySC: ${identityAddr}`);

  // ── 2. Deploy TradingSC ──
  console.log("\n2️⃣  Deploying TradingSC...");
  const tradingArt = loadArtifact("TradingSC");
  const TradingFactory = new ethers.ContractFactory(tradingArt.abi, tradingArt.bytecode, deployer);
  const tradingSC = await TradingFactory.deploy(identityAddr);
  await tradingSC.waitForDeployment();
  const tradingAddr = await tradingSC.getAddress();
  console.log(`   TradingSC:  ${tradingAddr}`);

  // ── 3. Register Pi device as a node ──
  console.log("\n3️⃣  Registering Pi device as a node...");
  const salt = ethers.hexlify(crypto.randomBytes(32));
  const pubkeyBytes = ethers.getBytes(piAddr);
  const nodeId = ethers.keccak256(ethers.concat([pubkeyBytes, ethers.getBytes(salt)]));
  const pubkeyHash = ethers.keccak256(pubkeyBytes);

  const registerTx = await identitySC.connect(piDevice).registerNode(nodeId, pubkeyHash, "ipfs://pi-device-meta");
  await registerTx.wait();
  console.log(`   nodeId: ${nodeId}`);
  console.log(`   Registered ✔`);

  // ── 4. Compose quantized offer ──
  console.log("\n4️⃣  Composing quantized offer...");
  const roundId     = 1;
  const kwhBucket   = 2;   // 0.5–1.0 kWh
  const priceBucket = 3;   // 10–20 cents
  const offerNonce  = 0;
  const currentBlock = await provider.getBlockNumber();
  const expiryBlock = currentBlock + 100;

  const offerStructHash = composeOfferStructHash(roundId, nodeId, kwhBucket, priceBucket, offerNonce, expiryBlock);
  console.log(`   roundId     : ${roundId}`);
  console.log(`   kwhBucket   : ${kwhBucket} (0.5–1.0 kWh)`);
  console.log(`   priceBucket : ${priceBucket} (10–20 cents)`);
  console.log(`   nonce       : ${offerNonce}`);
  console.log(`   expiryBlock : ${expiryBlock}`);
  console.log(`   offerHash   : ${offerStructHash}`);

  // ── 5. Sign offer via EIP-712 ──
  console.log("\n5️⃣  Signing offer (EIP-712)...");
  const domain = {
    name:              "GridGuardian-Trading",
    version:           "1",
    chainId:           31337,
    verifyingContract: tradingAddr,
  };
  const OFFER_TYPES = {
    Offer: [
      { name: "roundId",     type: "uint32"  },
      { name: "nodeId",      type: "bytes32" },
      { name: "kwhBucket",   type: "uint16"  },
      { name: "priceBucket", type: "uint16"  },
      { name: "nonce",       type: "uint32"  },
      { name: "expiryBlock", type: "uint32"  },
    ],
  };
  const offerValues = { roundId, nodeId, kwhBucket, priceBucket, nonce: offerNonce, expiryBlock };
  const signature = await piDevice.signTypedData(domain, OFFER_TYPES, offerValues);
  console.log(`   Signature: ${signature.slice(0, 20)}...`);

  // ── 6. Compute commit and post on-chain ──
  console.log("\n6️⃣  Computing commit and posting on-chain...");
  const commitSalt = ethers.hexlify(crypto.randomBytes(32));
  const commitHash = computeCommitHash(offerStructHash, commitSalt);
  console.log(`   salt       : ${commitSalt}`);
  console.log(`   commitHash : ${commitHash}`);

  const commitTx = await tradingSC.postCommit(
    commitHash, offerStructHash, roundId, nodeId, expiryBlock, signature
  );
  const commitReceipt = await commitTx.wait();
  console.log(`   tx: ${commitReceipt.hash}`);

  // Verify CommitPosted event
  const commitEvent = commitReceipt.logs.find(
    log => log.address.toLowerCase() === tradingAddr.toLowerCase()
  );
  if (commitEvent) {
    console.log(`   CommitPosted event emitted ✔`);
  }

  // Verify stored commit
  const storedCommit = await tradingSC.getCommit(commitHash);
  console.log(`   Stored owner     : ${storedCommit.owner}`);
  console.log(`   Stored roundId   : ${storedCommit.roundId}`);
  console.log(`   Stored revealed  : ${storedCommit.revealed}`);

  if (storedCommit.owner !== piAddr) throw new Error("Owner mismatch!");

  // ── 7. Broadcast offer off-chain (P2P dev stub) ──
  console.log("\n7️⃣  Broadcasting offer off-chain (P2P)...");
  const offerPayload = {
    roundId,
    nodeId,
    kwhBucket,
    priceBucket,
    nonce: offerNonce,
    expiryBlock,
    offerHash: offerStructHash,
    commitHash,
    signature,
    signer: piAddr,
  };
  console.log(`  [P2P] Offer broadcast (dev log):`);
  console.log(`    roundId     : ${offerPayload.roundId}`);
  console.log(`    kwhBucket   : ${offerPayload.kwhBucket}`);
  console.log(`    priceBucket : ${offerPayload.priceBucket}`);
  console.log(`    signer      : ${offerPayload.signer}`);

  // ── 8. Reveal the offer on-chain ──
  console.log("\n8️⃣  Revealing offer on-chain...");
  const revealTx = await tradingSC.revealOffer(commitHash, offerStructHash, commitSalt);
  const revealReceipt = await revealTx.wait();
  console.log(`   tx: ${revealReceipt.hash}`);

  // Verify OfferRevealed event
  const revealEvent = revealReceipt.logs.find(
    log => log.address.toLowerCase() === tradingAddr.toLowerCase()
  );
  if (revealEvent) {
    console.log(`   OfferRevealed event emitted ✔`);
  }

  // ── 9. Verify final state ──
  console.log("\n9️⃣  Verifying final state...");
  const isRevealed = await tradingSC.isCommitRevealed(commitHash);
  console.log(`   isCommitRevealed: ${isRevealed}`);

  if (!isRevealed) throw new Error("Commit not revealed!");

  const finalCommit = await tradingSC.getCommit(commitHash);
  console.log(`   Final revealed: ${finalCommit.revealed} ✔`);
  console.log(`   Final owner   : ${finalCommit.owner} ✔`);

  console.log("\n╔══════════════════════════════════════════════════════════════╗");
  console.log("║   ✅ All Step 3 integration checks PASSED!                  ║");
  console.log("╚══════════════════════════════════════════════════════════════╝");
}

main().catch((err) => {
  console.error("\n❌ Integration test failed:", err);
  process.exit(1);
});
