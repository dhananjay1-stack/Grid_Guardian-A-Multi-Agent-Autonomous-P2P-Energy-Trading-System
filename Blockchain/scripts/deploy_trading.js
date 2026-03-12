/**
 * scripts/deploy_trading.js — Deploy TradingSC
 *
 * Prerequisites:
 *   - IdentitySC already deployed (address in .env IDENTITY_SC_ADDRESS)
 *   - Hardhat node running on :8545
 *
 * Usage:
 *   npx hardhat run scripts/deploy_trading.js --network localhost
 */
require("dotenv").config();
const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);

  const identityAddr = process.env.IDENTITY_SC_ADDRESS;
  if (!identityAddr) {
    console.error("❌  IDENTITY_SC_ADDRESS not set in .env. Deploy IdentitySC first.");
    process.exit(1);
  }

  console.log("\nDeploying TradingSC (linked to IdentitySC)...");
  const TradingSC = await hre.ethers.getContractFactory("TradingSC");
  const trading = await TradingSC.deploy(identityAddr);
  await trading.waitForDeployment();
  const tradingAddr = await trading.getAddress();
  console.log(`   TradingSC deployed to: ${tradingAddr}`);

  console.log("\n✅  Update your .env:");
  console.log(`   TRADING_SC_ADDRESS=${tradingAddr}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
