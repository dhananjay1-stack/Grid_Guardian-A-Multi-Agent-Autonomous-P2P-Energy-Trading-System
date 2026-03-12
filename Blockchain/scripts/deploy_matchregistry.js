/**
 * scripts/deploy_matchregistry.js — Deploy MatchRegistry
 *
 * Prerequisites:
 *   - MockUSDC already deployed (address in .env MOCK_USDC_ADDRESS)
 *   - Hardhat node running on :8545
 *
 * Usage:
 *   npx hardhat run scripts/deploy_matchregistry.js --network localhost
 */
require("dotenv").config();
const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);

  const usdcAddr = process.env.MOCK_USDC_ADDRESS;
  if (!usdcAddr) {
    console.error("❌  MOCK_USDC_ADDRESS not set in .env. Deploy MockUSDC first.");
    process.exit(1);
  }

  // Default parameters
  const CHALLENGE_WINDOW = 60;             // 60 blocks
  const MATCH_BOND       = 100_000_000n;   // 100 USDC (6 decimals)
  const CHALLENGE_BOND   = 10_000_000n;    // 10 USDC
  const REQUIRED_SIGNERS = 2;              // 2-of-n quorum

  console.log("\nDeploying MatchRegistry...");
  console.log(`  stablecoin       : ${usdcAddr}`);
  console.log(`  challengeWindow  : ${CHALLENGE_WINDOW} blocks`);
  console.log(`  matchPublishBond : ${MATCH_BOND} (100 USDC)`);
  console.log(`  challengeBond    : ${CHALLENGE_BOND} (10 USDC)`);
  console.log(`  requiredSigners  : ${REQUIRED_SIGNERS}`);

  const MatchRegistry = await hre.ethers.getContractFactory("MatchRegistry");
  const registry = await MatchRegistry.deploy(
    usdcAddr,
    deployer.address,
    CHALLENGE_WINDOW,
    MATCH_BOND,
    CHALLENGE_BOND,
    REQUIRED_SIGNERS
  );
  await registry.waitForDeployment();
  const registryAddr = await registry.getAddress();
  console.log(`\n   MatchRegistry deployed to: ${registryAddr}`);

  console.log("\n✅  Update your .env:");
  console.log(`   MATCH_REGISTRY_ADDRESS=${registryAddr}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
