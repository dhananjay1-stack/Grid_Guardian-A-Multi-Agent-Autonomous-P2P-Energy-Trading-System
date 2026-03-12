/**
 * tests/trading.test.js — TradingSC unit tests (Hardhat + Chai)
 *
 * Covers:
 *   1.  postCommit stores commit, emits CommitPosted, owner = signer
 *   2.  postCommit rejects duplicate commitHash
 *   3.  postCommit rejects invalid signature
 *   4.  postCommit rejects unregistered node
 *   5.  postCommit rejects inactive (revoked) node
 *   6.  postCommit rejects mismatched nodeId/signer
 *   7.  revealOffer accepts valid reveal before expiry
 *   8.  revealOffer rejects mismatched salt/offerHash
 *   9.  revealOffer rejects reveal after expiry
 *  10.  revealOffer rejects double reveal
 *  11.  revealOffer rejects non-existent commit
 *  12.  Multiple commits from same node with different nonces
 *  13.  getCommit view helper returns correct data
 *  14.  getDomainSeparator returns non-zero bytes32
 */
const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("TradingSC", function () {
  let identitySC, tradingSC;
  let admin, device1, device2, outsider;
  let nodeId1, nodeId2;

  // EIP-712 types matching TradingSC.OFFER_TYPEHASH
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

  function makeTradingDomain(contractAddr) {
    return {
      name:              "GridGuardian-Trading",
      version:           "1",
      chainId:           31337,
      verifyingContract: contractAddr,
    };
  }

  // Derive nodeId & pubkeyHash from a signer address
  function deriveIds(signer, salt) {
    const addrBytes  = ethers.getBytes(signer.address);
    const nodeId     = ethers.keccak256(ethers.concat([addrBytes, salt]));
    const pubkeyHash = ethers.keccak256(addrBytes);
    return { nodeId, pubkeyHash };
  }

  // Compose an EIP-712 offer struct hash (same logic as compose_offer.js)
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

  // Compute commitHash = keccak256(offerHash | salt)
  function computeCommitHash(offerHash, salt) {
    return ethers.keccak256(
      ethers.solidityPacked(["bytes32", "bytes32"], [offerHash, salt])
    );
  }

  beforeEach(async function () {
    [admin, device1, device2, outsider] = await ethers.getSigners();

    // Deploy IdentitySC
    const IdentitySC = await ethers.getContractFactory("IdentitySC");
    identitySC = await IdentitySC.deploy(admin.address);
    await identitySC.waitForDeployment();

    // Deploy TradingSC
    const TradingSC = await ethers.getContractFactory("TradingSC");
    tradingSC = await TradingSC.deploy(await identitySC.getAddress());
    await tradingSC.waitForDeployment();

    // Register device1 as a node
    const salt1 = ethers.randomBytes(32);
    const ids1 = deriveIds(device1, salt1);
    nodeId1 = ids1.nodeId;
    await identitySC.connect(device1).registerNode(nodeId1, ids1.pubkeyHash, "ipfs://dev1");

    // Register device2 as a node
    const salt2 = ethers.randomBytes(32);
    const ids2 = deriveIds(device2, salt2);
    nodeId2 = ids2.nodeId;
    await identitySC.connect(device2).registerNode(nodeId2, ids2.pubkeyHash, "ipfs://dev2");
  });

  // ─────────────────────────────────────────────────────────────────
  //                      postCommit tests
  // ─────────────────────────────────────────────────────────────────

  it("1. postCommit stores commit and emits CommitPosted with correct owner", async function () {
    const roundId     = 1;
    const kwhBucket   = 2;
    const priceBucket = 3;
    const nonce       = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 100;

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt      = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await expect(
      tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig)
    )
      .to.emit(tradingSC, "CommitPosted")
      .withArgs(commitHash, device1.address, roundId, expiryBlock);

    // Verify stored commit
    const commit = await tradingSC.getCommit(commitHash);
    expect(commit.owner).to.equal(device1.address);
    expect(commit.roundId).to.equal(roundId);
    expect(commit.expiryBlock).to.equal(expiryBlock);
    expect(commit.revealed).to.be.false;
  });

  it("2. postCommit rejects duplicate commitHash", async function () {
    const roundId = 1, kwhBucket = 2, priceBucket = 3, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 100;

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig);

    await expect(
      tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig)
    ).to.be.revertedWithCustomError(tradingSC, "CommitAlreadyExists");
  });

  it("3. postCommit rejects invalid signature (wrong signer)", async function () {
    const roundId = 1, kwhBucket = 2, priceBucket = 3, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 100;

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    // outsider signs (not the node owner)
    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await outsider.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await expect(
      tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig)
    ).to.be.revertedWithCustomError(tradingSC, "SignerNotRegisteredNode");
  });

  it("4. postCommit rejects unregistered node ID", async function () {
    const roundId = 1, kwhBucket = 2, priceBucket = 3, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 100;

    const fakeNodeId = ethers.keccak256(ethers.toUtf8Bytes("fake-node"));
    const offerHash = composeOfferStructHash(roundId, fakeNodeId, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: fakeNodeId, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await expect(
      tradingSC.postCommit(commitHash, offerHash, roundId, fakeNodeId, expiryBlock, sig)
    ).to.be.revertedWithCustomError(tradingSC, "SignerNotRegisteredNode");
  });

  it("5. postCommit rejects inactive (revoked) node", async function () {
    // Revoke device1's node
    await identitySC.connect(admin).revokeNode(nodeId1);

    const roundId = 1, kwhBucket = 2, priceBucket = 3, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 100;

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await expect(
      tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig)
    ).to.be.revertedWithCustomError(tradingSC, "SignerNotRegisteredNode");
  });

  it("6. postCommit rejects mismatched nodeId/signer (device2 signs, device1 nodeId)", async function () {
    const roundId = 1, kwhBucket = 2, priceBucket = 3, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 100;

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    // device2 signs but using device1's nodeId
    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device2.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await expect(
      tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig)
    ).to.be.revertedWithCustomError(tradingSC, "SignerNotRegisteredNode");
  });

  // ─────────────────────────────────────────────────────────────────
  //                      revealOffer tests
  // ─────────────────────────────────────────────────────────────────

  it("7. revealOffer accepts valid reveal before expiry", async function () {
    const roundId = 1, kwhBucket = 2, priceBucket = 3, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 200;

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig);

    await expect(
      tradingSC.revealOffer(commitHash, offerHash, salt)
    )
      .to.emit(tradingSC, "OfferRevealed")
      .withArgs(commitHash, offerHash, ethers.keccak256(ethers.solidityPacked(["bytes32"], [salt])));

    expect(await tradingSC.isCommitRevealed(commitHash)).to.be.true;
  });

  it("8. revealOffer rejects mismatched salt", async function () {
    const roundId = 1, kwhBucket = 2, priceBucket = 3, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 200;

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig);

    // Wrong salt
    const wrongSalt = ethers.hexlify(ethers.randomBytes(32));
    await expect(
      tradingSC.revealOffer(commitHash, offerHash, wrongSalt)
    ).to.be.revertedWithCustomError(tradingSC, "CommitHashMismatch");
  });

  it("9. revealOffer rejects reveal after expiry", async function () {
    const roundId = 1, kwhBucket = 2, priceBucket = 3, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 5; // very short window

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig);

    // Mine blocks past expiry
    for (let i = 0; i < 10; i++) {
      await ethers.provider.send("evm_mine", []);
    }

    await expect(
      tradingSC.revealOffer(commitHash, offerHash, salt)
    ).to.be.revertedWithCustomError(tradingSC, "CommitExpired");
  });

  it("10. revealOffer rejects double reveal", async function () {
    const roundId = 1, kwhBucket = 2, priceBucket = 3, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 200;

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig);
    await tradingSC.revealOffer(commitHash, offerHash, salt);

    await expect(
      tradingSC.revealOffer(commitHash, offerHash, salt)
    ).to.be.revertedWithCustomError(tradingSC, "AlreadyRevealed");
  });

  it("11. revealOffer rejects non-existent commit", async function () {
    const fakeHash = ethers.keccak256(ethers.toUtf8Bytes("fake"));
    const fakeSalt = ethers.hexlify(ethers.randomBytes(32));

    await expect(
      tradingSC.revealOffer(fakeHash, fakeHash, fakeSalt)
    ).to.be.revertedWithCustomError(tradingSC, "NoCommitFound");
  });

  it("12. Multiple commits from same node with different nonces", async function () {
    const roundId = 1, kwhBucket = 2, priceBucket = 3;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 200;
    const domain = makeTradingDomain(await tradingSC.getAddress());

    // Commit with nonce 0
    const offerHash0 = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, 0, expiryBlock);
    const salt0 = ethers.hexlify(ethers.randomBytes(32));
    const commitHash0 = computeCommitHash(offerHash0, salt0);
    const sig0 = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce: 0, expiryBlock,
    });
    await tradingSC.postCommit(commitHash0, offerHash0, roundId, nodeId1, expiryBlock, sig0);

    // Commit with nonce 1
    const offerHash1 = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, 1, expiryBlock);
    const salt1 = ethers.hexlify(ethers.randomBytes(32));
    const commitHash1 = computeCommitHash(offerHash1, salt1);
    const sig1 = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce: 1, expiryBlock,
    });
    await tradingSC.postCommit(commitHash1, offerHash1, roundId, nodeId1, expiryBlock, sig1);

    // Both commits exist
    const c0 = await tradingSC.getCommit(commitHash0);
    const c1 = await tradingSC.getCommit(commitHash1);
    expect(c0.owner).to.equal(device1.address);
    expect(c1.owner).to.equal(device1.address);
    expect(commitHash0).to.not.equal(commitHash1);
  });

  it("13. getCommit view helper returns correct data", async function () {
    const roundId = 5, kwhBucket = 1, priceBucket = 4, nonce = 0;
    const currentBlock = await ethers.provider.getBlockNumber();
    const expiryBlock = currentBlock + 100;

    const offerHash = composeOfferStructHash(roundId, nodeId1, kwhBucket, priceBucket, nonce, expiryBlock);
    const salt = ethers.hexlify(ethers.randomBytes(32));
    const commitHash = computeCommitHash(offerHash, salt);

    const domain = makeTradingDomain(await tradingSC.getAddress());
    const sig = await device1.signTypedData(domain, OFFER_TYPES, {
      roundId, nodeId: nodeId1, kwhBucket, priceBucket, nonce, expiryBlock,
    });

    await tradingSC.postCommit(commitHash, offerHash, roundId, nodeId1, expiryBlock, sig);

    const commit = await tradingSC.getCommit(commitHash);
    expect(commit.owner).to.equal(device1.address);
    expect(commit.roundId).to.equal(roundId);
    expect(commit.expiryBlock).to.equal(expiryBlock);
    expect(commit.revealed).to.be.false;
  });

  it("14. getDomainSeparator returns non-zero bytes32", async function () {
    const ds = await tradingSC.getDomainSeparator();
    expect(ds).to.not.equal(ethers.ZeroHash);
    expect(ds.length).to.equal(66); // 0x + 64 hex chars
  });
});
