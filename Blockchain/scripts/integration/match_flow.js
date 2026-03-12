/**
 * scripts/integration/match_flow.js
 *
 * End-to-end integration test for Step 4: Optimizer Trust & Disputability
 *
 * Scenarios:
 *   A. Happy path: publish → window passes → finalize
 *   B. Valid challenge: publish → challenge → adjudicator accepts → optimizer slashed
 *   C. Frivolous challenge: publish → challenge → adjudicator rejects → challenger bond forfeited
 *
 * Prerequisites:
 *   - Hardhat node running on :8545
 *   - Contracts compiled (`npx hardhat compile`)
 *
 * Usage:
 *   node scripts/integration/match_flow.js
 */
require("dotenv").config();
const { ethers } = require("ethers");
const path = require("path");
const fs   = require("fs");

// ── Config ─────────────────────────────────────────────────────────
const RPC_URL          = process.env.RPC_URL || "http://127.0.0.1:8545";
const USDC_DECIMALS    = 6;
const ONE_USDC         = 10n ** BigInt(USDC_DECIMALS);
const MATCH_BOND       = 100n * ONE_USDC;
const CHALLENGE_BOND   = 10n  * ONE_USDC;
const CHALLENGE_WINDOW = 10;      // short window for integration test
const REQUIRED_SIGNERS = 2;       // 2-of-3

// ── Load artifact ──────────────────────────────────────────────────
function loadArtifact(name) {
  const p = path.resolve(__dirname, `../../artifacts/contracts/${name}.sol/${name}.json`);
  if (!fs.existsSync(p)) {
    console.error(`❌  Artifact not found: ${name}. Run \`npx hardhat compile\` first.`);
    process.exit(1);
  }
  const a = JSON.parse(fs.readFileSync(p, "utf-8"));
  return { abi: a.abi, bytecode: a.bytecode };
}

// ── EIP-712 ────────────────────────────────────────────────────────
const MATCHRESULT_TYPES = {
  MatchResult: [
    { name: "roundId",      type: "uint32"  },
    { name: "matchHash",    type: "bytes32" },
    { name: "inputsHash",   type: "bytes32" },
    { name: "publishNonce", type: "uint256" },
    { name: "timestamp",    type: "uint256" },
  ],
};

function getMatchDomain(verifyingContract) {
  return {
    name:              "GridGuardian-Match",
    version:           "1",
    chainId:           31337,
    verifyingContract,
  };
}

async function collectSigs(wallets, contractAddr, message) {
  const domain = getMatchDomain(contractAddr);
  const sigs = [];
  for (const w of wallets) {
    const sig = await w.signTypedData(domain, MATCHRESULT_TYPES, message);
    sigs.push(sig);
  }
  return "0x" + sigs.map(s => s.slice(2)).join("");
}

function computeInputsHash(commitHashes) {
  const sorted = [...commitHashes].sort();
  const packed = ethers.solidityPacked(
    sorted.map(() => "bytes32"),
    sorted
  );
  return ethers.keccak256(packed);
}

// ── Mine blocks helper ─────────────────────────────────────────────
async function mineBlocks(provider, count) {
  for (let i = 0; i < count; i++) {
    await provider.send("evm_mine", []);
  }
}

// ── Main ───────────────────────────────────────────────────────────
async function main() {
  console.log("╔══════════════════════════════════════════════════════════════╗");
  console.log("║   Step 4: Match Registry Integration Test                   ║");
  console.log("╚══════════════════════════════════════════════════════════════╝\n");

  const provider = new ethers.JsonRpcProvider(RPC_URL);

  // Signers: deployer=0, operator1=3, operator2=4, operator3=5,
  //          optimizer=6, challenger=7, adjudicator=deployer
  const deployer  = await provider.getSigner(0);
  const operator1 = await provider.getSigner(3);
  const operator2 = await provider.getSigner(4);
  const operator3 = await provider.getSigner(5);
  const optimizer = await provider.getSigner(6);
  const challenger= await provider.getSigner(7);

  const deployerAddr  = await deployer.getAddress();
  const op1Addr       = await operator1.getAddress();
  const op2Addr       = await operator2.getAddress();
  const op3Addr       = await operator3.getAddress();
  const optimizerAddr = await optimizer.getAddress();
  const challengerAddr= await challenger.getAddress();

  console.log(`  Deployer    : ${deployerAddr}`);
  console.log(`  Operator 1  : ${op1Addr}`);
  console.log(`  Operator 2  : ${op2Addr}`);
  console.log(`  Operator 3  : ${op3Addr}`);
  console.log(`  Optimizer   : ${optimizerAddr}`);
  console.log(`  Challenger  : ${challengerAddr}\n`);

  // ── 1. Deploy MockUSDC ──
  console.log("1️⃣  Deploying MockUSDC...");
  const usdcArt = loadArtifact("MockUSDC");
  const USDCFactory = new ethers.ContractFactory(usdcArt.abi, usdcArt.bytecode, deployer);
  const mockUSDC = await USDCFactory.deploy(deployerAddr);
  await mockUSDC.waitForDeployment();
  const usdcAddr = await mockUSDC.getAddress();
  console.log(`   MockUSDC: ${usdcAddr}`);

  // ── 2. Deploy MatchRegistry ──
  console.log("\n2️⃣  Deploying MatchRegistry...");
  const mrArt = loadArtifact("MatchRegistry");
  const MRFactory = new ethers.ContractFactory(mrArt.abi, mrArt.bytecode, deployer);
  const matchRegistry = await MRFactory.deploy(
    usdcAddr, deployerAddr, CHALLENGE_WINDOW, MATCH_BOND, CHALLENGE_BOND, REQUIRED_SIGNERS
  );
  await matchRegistry.waitForDeployment();
  const mrAddr = await matchRegistry.getAddress();
  console.log(`   MatchRegistry: ${mrAddr}`);

  // ── 3. Setup operators ──
  console.log("\n3️⃣  Adding operators...");
  await (await matchRegistry.addOperator(op1Addr)).wait();
  await (await matchRegistry.addOperator(op2Addr)).wait();
  await (await matchRegistry.addOperator(op3Addr)).wait();
  console.log(`   3 operators added ✔`);
  console.log(`   Operator count: ${await matchRegistry.operatorCount()}`);

  // ── 4. Mint USDC and deposit optimizer bond ──
  console.log("\n4️⃣  Minting USDC and depositing optimizer bond...");
  await (await mockUSDC.mint(optimizerAddr, 500n * ONE_USDC)).wait();
  await (await mockUSDC.mint(challengerAddr, 100n * ONE_USDC)).wait();

  await (await mockUSDC.connect(optimizer).approve(mrAddr, MATCH_BOND)).wait();
  const optId = ethers.keccak256(ethers.toUtf8Bytes("optimizer-1"));
  await (await matchRegistry.connect(optimizer).depositOptimizerBond(optId, MATCH_BOND)).wait();
  console.log(`   Optimizer bond: ${await matchRegistry.getOptimizerBond(optId)} (${MATCH_BOND} expected)`);

  // ════════════════════════════════════════════════════════════════════
  //   SCENARIO A: Happy Path (publish → window → finalize)
  // ════════════════════════════════════════════════════════════════════
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("  Scenario A: Happy Path");
  console.log("═══════════════════════════════════════════════════════════\n");

  console.log("5️⃣  Publishing match A...");
  const blobA = ethers.toUtf8Bytes(JSON.stringify({ roundId: 1, trades: [{ seller: "A", buyer: "B", kwh: 1.5 }] }));
  const matchHashA = ethers.keccak256(blobA);
  const commitHashesA = [
    ethers.keccak256(ethers.toUtf8Bytes("commitA-1")),
    ethers.keccak256(ethers.toUtf8Bytes("commitA-2")),
  ];
  const inputsHashA = computeInputsHash(commitHashesA);
  const nonceA = await matchRegistry.optimizerNonce(optId);
  const blockA = await provider.getBlock("latest");
  const sigTimestampA = blockA.timestamp;

  const messageA = {
    roundId: 1,
    matchHash: matchHashA,
    inputsHash: inputsHashA,
    publishNonce: nonceA,
    timestamp: sigTimestampA,
  };

  const sigsA = await collectSigs([operator1, operator2], mrAddr, messageA);
  const pubATx = await matchRegistry.connect(optimizer).publishMatch(
    matchHashA, inputsHashA, 1, optId, sigTimestampA, sigsA
  );
  const pubAReceipt = await pubATx.wait();
  console.log(`   Published ✔ (tx: ${pubAReceipt.hash.slice(0, 18)}...)`);

  const mA = await matchRegistry.getMatch(matchHashA);
  console.log(`   Status: ${mA.status} (expected 1 = Published)`);
  if (Number(mA.status) !== 1) throw new Error("Match A not Published!");

  console.log("\n6️⃣  Mining past challenge window...");
  await mineBlocks(provider, CHALLENGE_WINDOW + 1);
  console.log(`   ${CHALLENGE_WINDOW + 1} blocks mined ✔`);

  console.log("\n7️⃣  Finalizing match A...");
  const finATx = await matchRegistry.finalizeMatch(matchHashA, blobA);
  await finATx.wait();
  const mAFin = await matchRegistry.getMatch(matchHashA);
  console.log(`   Status: ${mAFin.status} (expected 3 = Finalized)`);
  if (Number(mAFin.status) !== 3) throw new Error("Match A not Finalized!");
  console.log("   ✅ Scenario A PASSED!\n");

  // ════════════════════════════════════════════════════════════════════
  //   SCENARIO B: Valid Challenge (publish → challenge → slash)
  // ════════════════════════════════════════════════════════════════════
  console.log("═══════════════════════════════════════════════════════════");
  console.log("  Scenario B: Valid Challenge");
  console.log("═══════════════════════════════════════════════════════════\n");

  // Deposit more bond for optimizer (since bond is still >= MATCH_BOND)
  const bondBefore = await matchRegistry.getOptimizerBond(optId);
  console.log(`   Optimizer bond: ${bondBefore}`);

  console.log("8️⃣  Publishing match B...");
  const blobB = ethers.toUtf8Bytes(JSON.stringify({ roundId: 2, trades: [{ seller: "C", buyer: "D", kwh: 2 }] }));
  const matchHashB = ethers.keccak256(blobB);
  const inputsHashB = computeInputsHash([
    ethers.keccak256(ethers.toUtf8Bytes("commitB-1")),
    ethers.keccak256(ethers.toUtf8Bytes("commitB-2")),
  ]);
  const nonceB = await matchRegistry.optimizerNonce(optId);
  const blockB = await provider.getBlock("latest");
  const sigTimestampB = blockB.timestamp;

  const sigsB = await collectSigs([operator1, operator3], mrAddr, {
    roundId: 2, matchHash: matchHashB, inputsHash: inputsHashB,
    publishNonce: nonceB, timestamp: sigTimestampB,
  });

  const pubBTx = await matchRegistry.connect(optimizer).publishMatch(
    matchHashB, inputsHashB, 2, optId, sigTimestampB, sigsB
  );
  await pubBTx.wait();
  console.log(`   Published ✔`);

  console.log("\n9️⃣  Challenger challenges match B...");
  const altMatchHashB = ethers.keccak256(ethers.toUtf8Bytes("alt-result-B"));
  await (await mockUSDC.connect(challenger).approve(mrAddr, CHALLENGE_BOND)).wait();
  const chBTx = await matchRegistry.connect(challenger).challengeMatch(matchHashB, altMatchHashB);
  await chBTx.wait();
  console.log(`   Challenged ✔`);

  const mBCh = await matchRegistry.getMatch(matchHashB);
  console.log(`   Status: ${mBCh.status} (expected 2 = Challenged)`);
  if (Number(mBCh.status) !== 2) throw new Error("Match B not Challenged!");

  console.log("\n🔟  Adjudicator resolves: challenge VALID, slash 20 USDC...");
  const slashAmount = 20n * ONE_USDC;
  const challengerBalBefore = await mockUSDC.balanceOf(challengerAddr);

  const resBTx = await matchRegistry.resolveChallenge(matchHashB, true, optId, slashAmount);
  await resBTx.wait();

  const mBRes = await matchRegistry.getMatch(matchHashB);
  console.log(`   Status: ${mBRes.status} (expected 4 = Invalidated)`);
  if (Number(mBRes.status) !== 4) throw new Error("Match B not Invalidated!");

  const bondAfter = await matchRegistry.getOptimizerBond(optId);
  console.log(`   Optimizer bond: ${bondBefore} → ${bondAfter} (slashed ${slashAmount})`);

  const challengerBalAfter = await mockUSDC.balanceOf(challengerAddr);
  console.log(`   Challenger balance: ${challengerBalBefore} → ${challengerBalAfter}`);
  const expectedReward = slashAmount + CHALLENGE_BOND;
  if (challengerBalAfter - challengerBalBefore !== expectedReward) {
    throw new Error(`Challenger reward mismatch! Expected +${expectedReward}, got +${challengerBalAfter - challengerBalBefore}`);
  }
  console.log("   ✅ Scenario B PASSED!\n");

  // ════════════════════════════════════════════════════════════════════
  //   SCENARIO C: Frivolous Challenge (publish → challenge → reject)
  // ════════════════════════════════════════════════════════════════════
  console.log("═══════════════════════════════════════════════════════════");
  console.log("  Scenario C: Frivolous Challenge");
  console.log("═══════════════════════════════════════════════════════════\n");

  // Top up optimizer bond if needed
  const bondC = await matchRegistry.getOptimizerBond(optId);
  if (bondC < MATCH_BOND) {
    const topUp = MATCH_BOND - bondC;
    await (await mockUSDC.connect(optimizer).approve(mrAddr, topUp)).wait();
    await (await matchRegistry.connect(optimizer).depositOptimizerBond(optId, topUp)).wait();
    console.log(`   Topped up optimizer bond by ${topUp}`);
  }

  console.log("1️⃣1️⃣  Publishing match C...");
  const blobC = ethers.toUtf8Bytes(JSON.stringify({ roundId: 3, trades: [{ seller: "E", buyer: "F", kwh: 3 }] }));
  const matchHashC = ethers.keccak256(blobC);
  const inputsHashC = computeInputsHash([
    ethers.keccak256(ethers.toUtf8Bytes("commitC-1")),
  ]);
  const nonceC = await matchRegistry.optimizerNonce(optId);
  const blockC = await provider.getBlock("latest");
  const sigTimestampC = blockC.timestamp;

  const sigsC = await collectSigs([operator2, operator3], mrAddr, {
    roundId: 3, matchHash: matchHashC, inputsHash: inputsHashC,
    publishNonce: nonceC, timestamp: sigTimestampC,
  });
  const pubCTx = await matchRegistry.connect(optimizer).publishMatch(
    matchHashC, inputsHashC, 3, optId, sigTimestampC, sigsC
  );
  await pubCTx.wait();
  console.log(`   Published ✔`);

  console.log("\n1️⃣2️⃣  Challenger files frivolous challenge...");
  const altMatchHashC = ethers.keccak256(ethers.toUtf8Bytes("alt-result-C"));
  // Mint more USDC for challenger (may have run low)
  await (await mockUSDC.mint(challengerAddr, CHALLENGE_BOND)).wait();
  await (await mockUSDC.connect(challenger).approve(mrAddr, CHALLENGE_BOND)).wait();
  const chCTx = await matchRegistry.connect(challenger).challengeMatch(matchHashC, altMatchHashC);
  await chCTx.wait();
  console.log(`   Challenged ✔`);

  console.log("\n1️⃣3️⃣  Adjudicator resolves: challenge INVALID (frivolous)...");
  const publisherBalBefore = await mockUSDC.balanceOf(optimizerAddr);

  const resCTx = await matchRegistry.resolveChallenge(matchHashC, false, optId, 0);
  await resCTx.wait();

  const mCRes = await matchRegistry.getMatch(matchHashC);
  console.log(`   Status: ${mCRes.status} (expected 1 = Published, can finalize)`);
  if (Number(mCRes.status) !== 1) throw new Error("Match C status incorrect after frivolous challenge!");

  const publisherBalAfter = await mockUSDC.balanceOf(optimizerAddr);
  console.log(`   Publisher balance: ${publisherBalBefore} → ${publisherBalAfter}`);
  if (publisherBalAfter - publisherBalBefore !== CHALLENGE_BOND) {
    throw new Error(`Publisher did not receive forfeited challenge bond!`);
  }
  console.log("   ✅ Scenario C PASSED!\n");

  // ── Summary ──
  console.log("╔══════════════════════════════════════════════════════════════╗");
  console.log("║   ✅ All Step 4 integration checks PASSED!                  ║");
  console.log("║   • Scenario A: Happy path (publish → finalize)             ║");
  console.log("║   • Scenario B: Valid challenge (slash optimizer)            ║");
  console.log("║   • Scenario C: Frivolous challenge (forfeit bond)          ║");
  console.log("╚══════════════════════════════════════════════════════════════╝");
}

main().catch((err) => {
  console.error("\n❌  Integration test FAILED:", err.message || err);
  process.exit(1);
});
