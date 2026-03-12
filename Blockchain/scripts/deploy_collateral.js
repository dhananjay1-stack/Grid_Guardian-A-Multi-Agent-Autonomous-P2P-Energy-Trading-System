/**
 * scripts/deploy_collateral.js — Deploy MockUSDC + CollateralSC
 *
 * Prerequisites:
 *   - IdentitySC already deployed (address in .env IDENTITY_SC_ADDRESS)
 *   - Hardhat node running on :8545
 *
 * Usage:
 *   npx hardhat run scripts/deploy_collateral.js --network localhost
 */
require("dotenv").config();
const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);

  // ── 1. Deploy MockUSDC ──
  console.log("\n1️⃣  Deploying MockUSDC...");
  const MockUSDC = await hre.ethers.getContractFactory("MockUSDC");
  const usdc = await MockUSDC.deploy(deployer.address);
  await usdc.waitForDeployment();
  const usdcAddr = await usdc.getAddress();
  console.log(`   MockUSDC deployed to: ${usdcAddr}`);

  // ── 2. Deploy CollateralSC (linked to IdentitySC + MockUSDC) ──
  const identityAddr = process.env.IDENTITY_SC_ADDRESS;
  if (!identityAddr) {
    console.error("❌  IDENTITY_SC_ADDRESS not set in .env. Deploy IdentitySC first.");
    process.exit(1);
  }

  console.log("\n2️⃣  Deploying CollateralSC...");
  const CollateralSC = await hre.ethers.getContractFactory("CollateralSC");
  const collateral = await CollateralSC.deploy(usdcAddr, identityAddr, deployer.address);
  await collateral.waitForDeployment();
  const collateralAddr = await collateral.getAddress();
  console.log(`   CollateralSC deployed to: ${collateralAddr}`);

  console.log("\n✅  Update your .env:");
  console.log(`   MOCK_USDC_ADDRESS=${usdcAddr}`);
  console.log(`   COLLATERAL_SC_ADDRESS=${collateralAddr}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
