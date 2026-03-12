/**
 * tests/collateral.test.js — CollateralSC + MockUSDC unit tests (Hardhat + Chai)
 *
 * Covers:
 *   1. MockUSDC mint & decimals
 *   2. Deposit increases deposits[nodeId]
 *   3. Deposit reverts for unregistered/inactive node
 *   4. approveRelayer only callable by node owner
 *   5. approveRelayer reverts for non-owner
 *   6. claimGas success flow
 *   7. claimGas reverts on expired voucher
 *   8. claimGas reverts for wrong relayer
 *   9. claimGas reverts on invalid signature
 *  10. claimGas reverts on insufficient allowance
 *  11. claimGas reverts on insufficient deposit
 *  12. claimGas reverts on replayed nonce
 *  13. Admin withdraw
 *  14. setMinDeposit (admin)
 */
const { expect } = require("chai");
const { ethers }  = require("hardhat");

describe("CollateralSC", function () {
  let identitySC, mockUSDC, collateralSC;
  let admin, relayer, device1, device2, outsider;
  let nodeId1, pubkeyHash1;

  // Constants
  const USDC_DECIMALS = 6;
  const ONE_USDC = 10n ** BigInt(USDC_DECIMALS); // 1e6
  const DEPOSIT_AMOUNT = 1000n * ONE_USDC;       // 1000 USDC
  const CLAIM_AMOUNT = 5n * ONE_USDC;             // 5 USDC

  // EIP-712 types for GasVoucher
  const VOUCHER_TYPES = {
    GasVoucher: [
      { name: "nodeId",   type: "bytes32"  },
      { name: "relayer",  type: "address"  },
      { name: "amount",   type: "uint256"  },
      { name: "maxGas",   type: "uint256"  },
      { name: "gasPrice", type: "uint256"  },
      { name: "nonce",    type: "uint256"  },
      { name: "expiry",   type: "uint256"  },
      { name: "txHash",   type: "bytes32"  },
    ],
  };

  function makeCollateralDomain(contractAddr) {
    return {
      name:              "GridGuardian-Collateral",
      version:           "1",
      chainId:           31337,
      verifyingContract: contractAddr,
    };
  }

  // Derive nodeId & pubkeyHash from address (same as identity.test.js)
  function deriveIds(signer, salt) {
    const addrBytes  = ethers.getBytes(signer.address);
    const nodeId     = ethers.keccak256(ethers.concat([addrBytes, salt]));
    const pubkeyHash = ethers.keccak256(addrBytes);
    return { nodeId, pubkeyHash };
  }

  beforeEach(async function () {
    [admin, relayer, device1, device2, outsider] = await ethers.getSigners();

    // Deploy IdentitySC
    const IdentitySC = await ethers.getContractFactory("IdentitySC");
    identitySC = await IdentitySC.deploy(admin.address);
    await identitySC.waitForDeployment();

    // Deploy MockUSDC
    const MockUSDC = await ethers.getContractFactory("MockUSDC");
    mockUSDC = await MockUSDC.deploy(admin.address);
    await mockUSDC.waitForDeployment();

    // Deploy CollateralSC
    const CollateralSC = await ethers.getContractFactory("CollateralSC");
    collateralSC = await CollateralSC.deploy(
      await mockUSDC.getAddress(),
      await identitySC.getAddress(),
      admin.address
    );
    await collateralSC.waitForDeployment();

    // Register device1 as a node in IdentitySC
    const salt = ethers.randomBytes(32);
    const ids = deriveIds(device1, salt);
    nodeId1 = ids.nodeId;
    pubkeyHash1 = ids.pubkeyHash;
    await identitySC.connect(device1).registerNode(nodeId1, pubkeyHash1, "ipfs://test");

    // Mint USDC to device1 and approve CollateralSC
    await mockUSDC.mint(device1.address, DEPOSIT_AMOUNT);
    await mockUSDC.connect(device1).approve(
      await collateralSC.getAddress(),
      DEPOSIT_AMOUNT
    );
  });

  // ────────────────────────────────────────────────────────────────
  //                       MOCK USDC
  // ────────────────────────────────────────────────────────────────

  it("1. MockUSDC has 6 decimals and admin has initial supply", async function () {
    expect(await mockUSDC.decimals()).to.equal(6);
    const balance = await mockUSDC.balanceOf(admin.address);
    expect(balance).to.equal(1_000_000n * ONE_USDC);
  });

  // ────────────────────────────────────────────────────────────────
  //                       DEPOSIT
  // ────────────────────────────────────────────────────────────────

  it("2. Deposit increases deposits[nodeId]", async function () {
    await collateralSC.connect(device1).deposit(nodeId1, DEPOSIT_AMOUNT);
    expect(await collateralSC.deposits(nodeId1)).to.equal(DEPOSIT_AMOUNT);
  });

  it("3. Deposit reverts for zero amount", async function () {
    await expect(
      collateralSC.connect(device1).deposit(nodeId1, 0)
    ).to.be.revertedWithCustomError(collateralSC, "ZeroAmount");
  });

  it("3b. Deposit reverts for unregistered node", async function () {
    const fakeId = ethers.keccak256(ethers.toUtf8Bytes("fake"));
    await expect(
      collateralSC.connect(device1).deposit(fakeId, 100)
    ).to.be.revertedWith("node not registered");
  });

  // ────────────────────────────────────────────────────────────────
  //                   RELAYER ALLOWANCE
  // ────────────────────────────────────────────────────────────────

  it("4. approveRelayer only callable by node owner", async function () {
    await collateralSC.connect(device1).approveRelayer(nodeId1, relayer.address, 500n * ONE_USDC);
    expect(
      await collateralSC.relayerAllowance(nodeId1, relayer.address)
    ).to.equal(500n * ONE_USDC);
  });

  it("5. approveRelayer reverts for non-owner", async function () {
    await expect(
      collateralSC.connect(outsider).approveRelayer(nodeId1, relayer.address, 500n * ONE_USDC)
    ).to.be.revertedWithCustomError(collateralSC, "NotNodeOwner");
  });

  // ────────────────────────────────────────────────────────────────
  //                  GAS VOUCHER CLAIM
  // ────────────────────────────────────────────────────────────────

  async function setupForClaim() {
    // deposit + approve relayer
    await collateralSC.connect(device1).deposit(nodeId1, DEPOSIT_AMOUNT);
    await collateralSC.connect(device1).approveRelayer(nodeId1, relayer.address, 100n * ONE_USDC);
  }

  async function signVoucher(wallet, voucher, contractAddr) {
    const domain = makeCollateralDomain(contractAddr);
    return wallet.signTypedData(domain, VOUCHER_TYPES, voucher);
  }

  function makeVoucher(overrides = {}) {
    const block = { timestamp: Math.floor(Date.now() / 1000) + 3600 };
    return {
      nodeId:   nodeId1,
      relayer:  relayer.address,
      amount:   CLAIM_AMOUNT,
      maxGas:   100000n,
      gasPrice: 20000000000n,
      nonce:    0n,
      expiry:   BigInt(block.timestamp),
      txHash:   ethers.ZeroHash,
      ...overrides,
    };
  }

  it("6. claimGas success flow", async function () {
    await setupForClaim();

    const voucher = makeVoucher();
    const collateralAddr = await collateralSC.getAddress();

    // device1 is a Hardhat signer, not ethers.Wallet, so we need a workaround:
    // Create a wallet with device1's private key for EIP-712 signing
    const device1Wallet = new ethers.Wallet(
      // Get the private key for signer index 2 (device1 is signers[2])
      // Hardhat default accounts: index 0-19
      "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
      ethers.provider
    );

    // Actually we need to use device1 which is signers[2]. But signTypedData works on HardhatEthersSigner
    const sig = await device1.signTypedData(
      makeCollateralDomain(collateralAddr),
      VOUCHER_TYPES,
      voucher
    );

    const relayerBalBefore = await mockUSDC.balanceOf(relayer.address);
    await collateralSC.connect(relayer).claimGas(voucher, sig);
    const relayerBalAfter = await mockUSDC.balanceOf(relayer.address);

    expect(relayerBalAfter - relayerBalBefore).to.equal(CLAIM_AMOUNT);
    expect(await collateralSC.deposits(nodeId1)).to.equal(DEPOSIT_AMOUNT - CLAIM_AMOUNT);
    expect(await collateralSC.relayerAllowance(nodeId1, relayer.address)).to.equal(100n * ONE_USDC - CLAIM_AMOUNT);
    expect(await collateralSC.voucherNonce(nodeId1)).to.equal(1);
  });

  it("7. claimGas reverts on expired voucher", async function () {
    await setupForClaim();

    const voucher = makeVoucher({ expiry: 1n }); // already expired
    const collateralAddr = await collateralSC.getAddress();
    const sig = await device1.signTypedData(
      makeCollateralDomain(collateralAddr),
      VOUCHER_TYPES,
      voucher
    );

    await expect(
      collateralSC.connect(relayer).claimGas(voucher, sig)
    ).to.be.revertedWithCustomError(collateralSC, "VoucherExpired");
  });

  it("8. claimGas reverts for wrong relayer", async function () {
    await setupForClaim();

    const voucher = makeVoucher();
    const collateralAddr = await collateralSC.getAddress();
    const sig = await device1.signTypedData(
      makeCollateralDomain(collateralAddr),
      VOUCHER_TYPES,
      voucher
    );

    // outsider tries to claim (not the relayer in the voucher)
    await expect(
      collateralSC.connect(outsider).claimGas(voucher, sig)
    ).to.be.revertedWithCustomError(collateralSC, "InvalidRelayer");
  });

  it("9. claimGas reverts on invalid signature", async function () {
    await setupForClaim();

    const voucher = makeVoucher();
    const collateralAddr = await collateralSC.getAddress();

    // outsider signs instead of device1 (node owner)
    const sig = await outsider.signTypedData(
      makeCollateralDomain(collateralAddr),
      VOUCHER_TYPES,
      voucher
    );

    await expect(
      collateralSC.connect(relayer).claimGas(voucher, sig)
    ).to.be.revertedWithCustomError(collateralSC, "InvalidVoucherSignature");
  });

  it("10. claimGas reverts on insufficient allowance", async function () {
    await collateralSC.connect(device1).deposit(nodeId1, DEPOSIT_AMOUNT);
    await collateralSC.connect(device1).approveRelayer(nodeId1, relayer.address, 1n); // only 1 unit

    const voucher = makeVoucher(); // claims CLAIM_AMOUNT > 1
    const collateralAddr = await collateralSC.getAddress();
    const sig = await device1.signTypedData(
      makeCollateralDomain(collateralAddr),
      VOUCHER_TYPES,
      voucher
    );

    await expect(
      collateralSC.connect(relayer).claimGas(voucher, sig)
    ).to.be.revertedWithCustomError(collateralSC, "AllowanceExceeded");
  });

  it("11. claimGas reverts on insufficient deposit", async function () {
    // deposit small amount, approve large
    await collateralSC.connect(device1).deposit(nodeId1, 1n);
    await collateralSC.connect(device1).approveRelayer(nodeId1, relayer.address, 1000n * ONE_USDC);

    const voucher = makeVoucher(); // claims CLAIM_AMOUNT > 1
    const collateralAddr = await collateralSC.getAddress();
    const sig = await device1.signTypedData(
      makeCollateralDomain(collateralAddr),
      VOUCHER_TYPES,
      voucher
    );

    await expect(
      collateralSC.connect(relayer).claimGas(voucher, sig)
    ).to.be.revertedWithCustomError(collateralSC, "InsufficientDeposit");
  });

  it("12. claimGas reverts on replayed nonce", async function () {
    await setupForClaim();

    const voucher = makeVoucher();
    const collateralAddr = await collateralSC.getAddress();
    const sig = await device1.signTypedData(
      makeCollateralDomain(collateralAddr),
      VOUCHER_TYPES,
      voucher
    );

    // First claim succeeds
    await collateralSC.connect(relayer).claimGas(voucher, sig);

    // Sign same voucher again (nonce 0, but contract nonce is now 1)
    const sig2 = await device1.signTypedData(
      makeCollateralDomain(collateralAddr),
      VOUCHER_TYPES,
      voucher
    );

    await expect(
      collateralSC.connect(relayer).claimGas(voucher, sig2)
    ).to.be.revertedWithCustomError(collateralSC, "InvalidVoucherNonce");
  });

  // ────────────────────────────────────────────────────────────────
  //                     ADMIN FUNCTIONS
  // ────────────────────────────────────────────────────────────────

  it("13. Admin can withdraw from deposit", async function () {
    await collateralSC.connect(device1).deposit(nodeId1, DEPOSIT_AMOUNT);

    const before = await mockUSDC.balanceOf(admin.address);
    await collateralSC.connect(admin).withdraw(nodeId1, 100n * ONE_USDC, admin.address);
    const after = await mockUSDC.balanceOf(admin.address);

    expect(after - before).to.equal(100n * ONE_USDC);
    expect(await collateralSC.deposits(nodeId1)).to.equal(DEPOSIT_AMOUNT - 100n * ONE_USDC);
  });

  it("14. setMinDeposit updates minDeposit (admin only)", async function () {
    await collateralSC.connect(admin).setMinDeposit(50n * ONE_USDC);
    expect(await collateralSC.minDeposit()).to.equal(50n * ONE_USDC);

    // non-admin reverts
    await expect(
      collateralSC.connect(outsider).setMinDeposit(1n)
    ).to.be.reverted;
  });

  // ────────────────────────────────────────────────────────────────
  //                     VIEW HELPERS
  // ────────────────────────────────────────────────────────────────

  it("getDeposit returns correct balance", async function () {
    await collateralSC.connect(device1).deposit(nodeId1, 500n * ONE_USDC);
    expect(await collateralSC.getDeposit(nodeId1)).to.equal(500n * ONE_USDC);
  });

  it("GasClaimed event emitted on successful claim", async function () {
    await setupForClaim();

    const voucher = makeVoucher();
    const collateralAddr = await collateralSC.getAddress();
    const sig = await device1.signTypedData(
      makeCollateralDomain(collateralAddr),
      VOUCHER_TYPES,
      voucher
    );

    await expect(collateralSC.connect(relayer).claimGas(voucher, sig))
      .to.emit(collateralSC, "GasClaimed")
      .withArgs(nodeId1, relayer.address, CLAIM_AMOUNT, 0);
  });
});
