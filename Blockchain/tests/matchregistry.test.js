/**
 * tests/matchregistry.test.js — MatchRegistry unit tests (Hardhat + Chai)
 *
 * Covers:
 *   1.  depositOptimizerBond stores bond and emits event
 *   2.  publishMatch stores metadata, emits MatchPublished
 *   3.  publishMatch rejects duplicate matchHash
 *   4.  publishMatch rejects insufficient optimizer bond
 *   5.  publishMatch rejects insufficient operator signatures
 *   6.  challengeMatch within window emits MatchChallenged
 *   7.  challengeMatch rejects after window closes
 *   8.  challengeMatch rejects duplicate challenge
 *   9.  finalizeMatch after window succeeds
 *  10.  finalizeMatch before window reverts
 *  11.  finalizeMatch with wrong blob reverts
 *  12.  resolveChallenge (valid) slashes optimizer, rewards challenger
 *  13.  resolveChallenge (invalid) forfeits challenger bond
 *  14.  slashOptimizer admin function
 *  15.  operator management (add/remove)
 *  16.  admin config updates
 *  17.  getMatch and getChallenge view helpers
 */
const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("MatchRegistry", function () {
  let mockUSDC, matchRegistry;
  let admin, operator1, operator2, operator3, optimizer, challenger, outsider;

  const USDC_DECIMALS     = 6;
  const ONE_USDC          = 10n ** BigInt(USDC_DECIMALS);
  const MATCH_BOND        = 100n * ONE_USDC;        // 100 USDC
  const CHALLENGE_BOND    = 10n * ONE_USDC;          // 10 USDC
  const CHALLENGE_WINDOW  = 60;                      // 60 blocks
  const REQUIRED_SIGNERS  = 2;                       // 2-of-3

  const MATCHRESULT_TYPES = {
    MatchResult: [
      { name: "roundId",      type: "uint32"  },
      { name: "matchHash",    type: "bytes32" },
      { name: "inputsHash",   type: "bytes32" },
      { name: "publishNonce", type: "uint256" },
      { name: "timestamp",    type: "uint256" },
    ],
  };

  function makeMatchDomain(contractAddr) {
    return {
      name:              "GridGuardian-Match",
      version:           "1",
      chainId:           31337,
      verifyingContract: contractAddr,
    };
  }

  // Generate a deterministic match blob and its hash
  function createMatchBlob(roundId, trades) {
    const blob = ethers.toUtf8Bytes(JSON.stringify({ roundId, trades }));
    const matchHash = ethers.keccak256(blob);
    return { blob, matchHash };
  }

  // Generate inputsHash from a list of commit hashes
  function computeInputsHash(commitHashes) {
    const sorted = [...commitHashes].sort();
    const packed = ethers.solidityPacked(
      sorted.map(() => "bytes32"),
      sorted
    );
    return ethers.keccak256(packed);
  }

  // Collect operator EIP-712 signatures
  async function collectSigs(signers, contractAddr, message) {
    const domain = makeMatchDomain(contractAddr);
    const sigs = [];
    for (const s of signers) {
      const sig = await s.signTypedData(domain, MATCHRESULT_TYPES, message);
      sigs.push(sig);
    }
    return "0x" + sigs.map(s => s.slice(2)).join("");
  }

  let optimizerId;

  beforeEach(async function () {
    [admin, operator1, operator2, operator3, optimizer, challenger, outsider] = await ethers.getSigners();

    // Deploy MockUSDC
    const MockUSDC = await ethers.getContractFactory("MockUSDC");
    mockUSDC = await MockUSDC.deploy(admin.address);
    await mockUSDC.waitForDeployment();

    // Deploy MatchRegistry
    const MatchRegistry = await ethers.getContractFactory("MatchRegistry");
    matchRegistry = await MatchRegistry.deploy(
      await mockUSDC.getAddress(),
      admin.address,
      CHALLENGE_WINDOW,
      MATCH_BOND,
      CHALLENGE_BOND,
      REQUIRED_SIGNERS
    );
    await matchRegistry.waitForDeployment();

    // Setup operators (3 operators, need 2-of-3)
    await matchRegistry.connect(admin).addOperator(operator1.address);
    await matchRegistry.connect(admin).addOperator(operator2.address);
    await matchRegistry.connect(admin).addOperator(operator3.address);

    // Mint USDC to optimizer and challenger
    await mockUSDC.mint(optimizer.address, 1000n * ONE_USDC);
    await mockUSDC.mint(challenger.address, 100n * ONE_USDC);

    // Optimizer deposits bond
    optimizerId = ethers.keccak256(ethers.toUtf8Bytes("optimizer-1"));
    await mockUSDC.connect(optimizer).approve(await matchRegistry.getAddress(), MATCH_BOND);
    await matchRegistry.connect(optimizer).depositOptimizerBond(optimizerId, MATCH_BOND);
  });

  // Helper: publish a match with valid signatures
  async function publishTestMatch(roundId = 1, matchBlobStr = '{"trades":[{"a":"b"}]}') {
    const blob = ethers.toUtf8Bytes(matchBlobStr);
    const matchHash = ethers.keccak256(blob);
    const commitHashes = [
      ethers.keccak256(ethers.toUtf8Bytes("commit1")),
      ethers.keccak256(ethers.toUtf8Bytes("commit2")),
    ];
    const inputsHash = computeInputsHash(commitHashes);
    const nonce = await matchRegistry.optimizerNonce(optimizerId);

    // Use a fixed timestamp that both signers and the contract agree on
    const block = await ethers.provider.getBlock("latest");
    const sigTimestamp = block.timestamp;

    const message = {
      roundId,
      matchHash,
      inputsHash,
      publishNonce: nonce,
      timestamp: sigTimestamp,
    };

    const sigs = await collectSigs(
      [operator1, operator2],
      await matchRegistry.getAddress(),
      message
    );

    const tx = await matchRegistry.connect(optimizer).publishMatch(
      matchHash, inputsHash, roundId, optimizerId, sigTimestamp, sigs
    );
    const receipt = await tx.wait();

    return { matchHash, inputsHash, blob, receipt, roundId };
  }

  // ─────────────────────────────────────────────────────────────────
  //                     Bond tests
  // ─────────────────────────────────────────────────────────────────

  it("1. depositOptimizerBond stores bond and emits event", async function () {
    const bond = await matchRegistry.getOptimizerBond(optimizerId);
    expect(bond).to.equal(MATCH_BOND);
  });

  // ─────────────────────────────────────────────────────────────────
  //                    publishMatch tests
  // ─────────────────────────────────────────────────────────────────

  it("2. publishMatch stores metadata and emits MatchPublished", async function () {
    const { matchHash, inputsHash, roundId, receipt } = await publishTestMatch();

    // Verify event
    const mrAddr = matchRegistry.target || await matchRegistry.getAddress();
    const event = receipt.logs.find(l =>
      l.address.toLowerCase() === mrAddr.toLowerCase()
    );
    expect(event).to.not.be.undefined;

    // Verify stored match
    const m = await matchRegistry.getMatch(matchHash);
    expect(m.publisher).to.equal(optimizer.address);
    expect(m.inputsHash).to.equal(inputsHash);
    expect(m.roundId).to.equal(roundId);
    expect(m.status).to.equal(1); // Published
    expect(m.validSigners).to.be.gte(REQUIRED_SIGNERS);
  });

  it("3. publishMatch rejects duplicate matchHash", async function () {
    const { matchHash, inputsHash } = await publishTestMatch();

    const nonce = await matchRegistry.optimizerNonce(optimizerId);
    const block = await ethers.provider.getBlock("latest");
    const sigTimestamp = block.timestamp;
    const sigs = await collectSigs(
      [operator1, operator2],
      await matchRegistry.getAddress(),
      { roundId: 1, matchHash, inputsHash, publishNonce: nonce, timestamp: sigTimestamp }
    );

    await expect(
      matchRegistry.connect(optimizer).publishMatch(matchHash, inputsHash, 1, optimizerId, sigTimestamp, sigs)
    ).to.be.revertedWithCustomError(matchRegistry, "MatchAlreadyPublished");
  });

  it("4. publishMatch rejects insufficient optimizer bond", async function () {
    const newOptId = ethers.keccak256(ethers.toUtf8Bytes("no-bond-optimizer"));
    const blob = ethers.toUtf8Bytes('{"test":true}');
    const matchHash = ethers.keccak256(blob);
    const inputsHash = ethers.keccak256(ethers.toUtf8Bytes("inputs"));

    const block = await ethers.provider.getBlock("latest");
    const sigTimestamp = block.timestamp;
    const sigs = await collectSigs(
      [operator1, operator2],
      await matchRegistry.getAddress(),
      { roundId: 1, matchHash, inputsHash, publishNonce: 0, timestamp: sigTimestamp }
    );

    await expect(
      matchRegistry.connect(optimizer).publishMatch(matchHash, inputsHash, 1, newOptId, sigTimestamp, sigs)
    ).to.be.revertedWithCustomError(matchRegistry, "InsufficientOptimizerBond");
  });

  it("5. publishMatch rejects insufficient operator signatures (0 of 2 required)", async function () {
    const blob = ethers.toUtf8Bytes('{"trades":[]}');
    const matchHash = ethers.keccak256(blob);
    const inputsHash = ethers.keccak256(ethers.toUtf8Bytes("inputs"));

    // Sign with outsider (not an operator)
    const block = await ethers.provider.getBlock("latest");
    const sigTimestamp = block.timestamp;
    const sigs = await collectSigs(
      [outsider],
      await matchRegistry.getAddress(),
      { roundId: 1, matchHash, inputsHash, publishNonce: 0, timestamp: sigTimestamp }
    );

    await expect(
      matchRegistry.connect(optimizer).publishMatch(matchHash, inputsHash, 1, optimizerId, sigTimestamp, sigs)
    ).to.be.revertedWithCustomError(matchRegistry, "InsufficientSigners");
  });

  // ─────────────────────────────────────────────────────────────────
  //                   challengeMatch tests
  // ─────────────────────────────────────────────────────────────────

  it("6. challengeMatch within window emits MatchChallenged", async function () {
    const { matchHash } = await publishTestMatch();
    const altMatchHash = ethers.keccak256(ethers.toUtf8Bytes("alt-match"));

    // Approve challenge bond
    await mockUSDC.connect(challenger).approve(await matchRegistry.getAddress(), CHALLENGE_BOND);

    await expect(
      matchRegistry.connect(challenger).challengeMatch(matchHash, altMatchHash)
    )
      .to.emit(matchRegistry, "MatchChallenged")
      .withArgs(matchHash, altMatchHash, challenger.address, CHALLENGE_BOND);
  });

  it("7. challengeMatch rejects after window closes", async function () {
    const { matchHash } = await publishTestMatch();
    const altMatchHash = ethers.keccak256(ethers.toUtf8Bytes("alt-match"));

    // Mine past challenge window
    for (let i = 0; i < CHALLENGE_WINDOW + 1; i++) {
      await ethers.provider.send("evm_mine", []);
    }

    await mockUSDC.connect(challenger).approve(await matchRegistry.getAddress(), CHALLENGE_BOND);

    await expect(
      matchRegistry.connect(challenger).challengeMatch(matchHash, altMatchHash)
    ).to.be.revertedWithCustomError(matchRegistry, "ChallengeWindowClosed");
  });

  it("8. challengeMatch rejects duplicate challenge", async function () {
    const { matchHash } = await publishTestMatch();
    const altMatchHash = ethers.keccak256(ethers.toUtf8Bytes("alt-match"));

    await mockUSDC.connect(challenger).approve(await matchRegistry.getAddress(), CHALLENGE_BOND * 2n);
    await matchRegistry.connect(challenger).challengeMatch(matchHash, altMatchHash);

    await expect(
      matchRegistry.connect(challenger).challengeMatch(matchHash, altMatchHash)
    ).to.be.revertedWithCustomError(matchRegistry, "MatchNotPublished");
  });

  // ─────────────────────────────────────────────────────────────────
  //                   finalizeMatch tests
  // ─────────────────────────────────────────────────────────────────

  it("9. finalizeMatch after window succeeds", async function () {
    const matchBlobStr = '{"trades":[{"seller":"A","buyer":"B","kwh":1}]}';
    const { matchHash, blob } = await publishTestMatch(1, matchBlobStr);

    // Mine past challenge window
    for (let i = 0; i < CHALLENGE_WINDOW + 1; i++) {
      await ethers.provider.send("evm_mine", []);
    }

    await expect(
      matchRegistry.finalizeMatch(matchHash, blob)
    )
      .to.emit(matchRegistry, "MatchFinalized")
      .withArgs(matchHash, admin.address);

    const m = await matchRegistry.getMatch(matchHash);
    expect(m.status).to.equal(3); // Finalized
  });

  it("10. finalizeMatch before window reverts", async function () {
    const matchBlobStr = '{"trades":[]}';
    const { matchHash, blob } = await publishTestMatch(1, matchBlobStr);

    await expect(
      matchRegistry.finalizeMatch(matchHash, blob)
    ).to.be.revertedWithCustomError(matchRegistry, "ChallengeWindowOpen");
  });

  it("11. finalizeMatch with wrong blob reverts", async function () {
    const { matchHash } = await publishTestMatch();

    // Mine past window
    for (let i = 0; i < CHALLENGE_WINDOW + 1; i++) {
      await ethers.provider.send("evm_mine", []);
    }

    const wrongBlob = ethers.toUtf8Bytes("wrong blob data");
    await expect(
      matchRegistry.finalizeMatch(matchHash, wrongBlob)
    ).to.be.revertedWithCustomError(matchRegistry, "MatchBlobMismatch");
  });

  // ─────────────────────────────────────────────────────────────────
  //                   resolveChallenge tests
  // ─────────────────────────────────────────────────────────────────

  it("12. resolveChallenge (valid) slashes optimizer, rewards challenger", async function () {
    const { matchHash } = await publishTestMatch();
    const altMatchHash = ethers.keccak256(ethers.toUtf8Bytes("alt-match"));

    // Challenge
    await mockUSDC.connect(challenger).approve(await matchRegistry.getAddress(), CHALLENGE_BOND);
    await matchRegistry.connect(challenger).challengeMatch(matchHash, altMatchHash);

    const challengerBalBefore = await mockUSDC.balanceOf(challenger.address);
    const bondBefore = await matchRegistry.getOptimizerBond(optimizerId);

    // Adjudicator resolves: challenge is valid, slash 20 USDC
    const slashAmount = 20n * ONE_USDC;
    await matchRegistry.connect(admin).resolveChallenge(matchHash, true, optimizerId, slashAmount);

    // Verify: optimizer bond decreased
    const bondAfter = await matchRegistry.getOptimizerBond(optimizerId);
    expect(bondAfter).to.equal(bondBefore - slashAmount);

    // Verify: challenger received slash + bond refund
    const challengerBalAfter = await mockUSDC.balanceOf(challenger.address);
    expect(challengerBalAfter).to.equal(challengerBalBefore + slashAmount + CHALLENGE_BOND);

    // Verify: match invalidated
    const m = await matchRegistry.getMatch(matchHash);
    expect(m.status).to.equal(4); // Invalidated
  });

  it("13. resolveChallenge (invalid/frivolous) forfeits challenger bond to publisher", async function () {
    const { matchHash } = await publishTestMatch();
    const altMatchHash = ethers.keccak256(ethers.toUtf8Bytes("alt-match"));

    await mockUSDC.connect(challenger).approve(await matchRegistry.getAddress(), CHALLENGE_BOND);
    await matchRegistry.connect(challenger).challengeMatch(matchHash, altMatchHash);

    const publisherBalBefore = await mockUSDC.balanceOf(optimizer.address);

    // Adjudicator resolves: challenge is frivolous
    await matchRegistry.connect(admin).resolveChallenge(matchHash, false, optimizerId, 0);

    // Publisher receives forfeited challenge bond
    const publisherBalAfter = await mockUSDC.balanceOf(optimizer.address);
    expect(publisherBalAfter).to.equal(publisherBalBefore + CHALLENGE_BOND);

    // Match status reverted to Published (can be finalized)
    const m = await matchRegistry.getMatch(matchHash);
    expect(m.status).to.equal(1); // Published
  });

  // ─────────────────────────────────────────────────────────────────
  //                   slashOptimizer tests
  // ─────────────────────────────────────────────────────────────────

  it("14. slashOptimizer decreases bond and transfers to recipient", async function () {
    const slashAmount = 50n * ONE_USDC;
    const bondBefore = await matchRegistry.getOptimizerBond(optimizerId);

    await expect(
      matchRegistry.connect(admin).slashOptimizer(optimizerId, outsider.address, slashAmount)
    )
      .to.emit(matchRegistry, "OptimizerSlashed")
      .withArgs(optimizerId, outsider.address, slashAmount);

    expect(await matchRegistry.getOptimizerBond(optimizerId)).to.equal(bondBefore - slashAmount);
    expect(await mockUSDC.balanceOf(outsider.address)).to.equal(slashAmount);
  });

  // ─────────────────────────────────────────────────────────────────
  //                   Operator management tests
  // ─────────────────────────────────────────────────────────────────

  it("15. operator management: add and remove", async function () {
    expect(await matchRegistry.operators(operator1.address)).to.be.true;
    expect(await matchRegistry.operatorCount()).to.equal(3);

    await matchRegistry.connect(admin).removeOperator(operator3.address);
    expect(await matchRegistry.operators(operator3.address)).to.be.false;
    expect(await matchRegistry.operatorCount()).to.equal(2);

    await matchRegistry.connect(admin).addOperator(operator3.address);
    expect(await matchRegistry.operatorCount()).to.equal(3);
  });

  // ─────────────────────────────────────────────────────────────────
  //                   Config tests
  // ─────────────────────────────────────────────────────────────────

  it("16. admin can update config parameters", async function () {
    await matchRegistry.connect(admin).setChallengeWindow(120);
    expect(await matchRegistry.challengeWindowBlocks()).to.equal(120);

    await matchRegistry.connect(admin).setMatchPublishBond(200n * ONE_USDC);
    expect(await matchRegistry.matchPublishBond()).to.equal(200n * ONE_USDC);

    await matchRegistry.connect(admin).setChallengeBond(20n * ONE_USDC);
    expect(await matchRegistry.challengeBond()).to.equal(20n * ONE_USDC);

    await matchRegistry.connect(admin).setRequiredSigners(3);
    expect(await matchRegistry.requiredSigners()).to.equal(3);
  });

  // ─────────────────────────────────────────────────────────────────
  //                   View helper tests
  // ─────────────────────────────────────────────────────────────────

  it("17. getMatch and getChallenge return correct data", async function () {
    const { matchHash, inputsHash } = await publishTestMatch();
    const altMatchHash = ethers.keccak256(ethers.toUtf8Bytes("alt"));

    await mockUSDC.connect(challenger).approve(await matchRegistry.getAddress(), CHALLENGE_BOND);
    await matchRegistry.connect(challenger).challengeMatch(matchHash, altMatchHash);

    const m = await matchRegistry.getMatch(matchHash);
    expect(m.publisher).to.equal(optimizer.address);
    expect(m.inputsHash).to.equal(inputsHash);

    const ch = await matchRegistry.getChallenge(matchHash);
    expect(ch.challenger).to.equal(challenger.address);
    expect(ch.altMatchHash).to.equal(altMatchHash);
    expect(ch.bondAmount).to.equal(CHALLENGE_BOND);
    expect(ch.resolved).to.be.false;
  });
});
