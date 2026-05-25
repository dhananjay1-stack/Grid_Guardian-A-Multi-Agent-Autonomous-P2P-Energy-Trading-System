/**
 * scripts/deploy_settlement.js — Deploy SettlementSC + DeliveryRegistry
 *
 * Prerequisites:
 *   - MockUSDC already deployed (address in .env MOCK_USDC_ADDRESS)
 *   - IdentitySC already deployed (address in .env IDENTITY_SC_ADDRESS)
 *   - CollateralSC already deployed (address in .env COLLATERAL_SC_ADDRESS)
 *   - Hardhat node running on :8545
 *
 * Usage:
 *   npx hardhat run scripts/deploy_settlement.js --network localhost
 */
require("dotenv").config();
const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);

  // Validate environment
  const identityAddr = process.env.IDENTITY_SC_ADDRESS;
  const collateralAddr = process.env.COLLATERAL_SC_ADDRESS;

  if (!identityAddr) {
    console.error("❌  IDENTITY_SC_ADDRESS not set in .env. Deploy IdentitySC first.");
    process.exit(1);
  }
  if (!collateralAddr) {
    console.error("❌  COLLATERAL_SC_ADDRESS not set in .env. Deploy CollateralSC first.");
    process.exit(1);
  }

  // Default parameters
  const SETTLEMENT_TIMEOUT = 120;    // 120 blocks until buyer can refund
  const DISPUTE_WINDOW     = 60;     // 60 blocks after delivery to dispute
  const REQUIRED_RECEIPTS  = 1;      // single-party for pilot

  // ═══════════════════════════════════════════════════════════════════════════
  // Deploy SettlementSC
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log("Deploying SettlementSC...");
  console.log(`  collateralSC      : ${collateralAddr}`);
  console.log(`  identitySC        : ${identityAddr}`);
  console.log(`  settlementTimeout : ${SETTLEMENT_TIMEOUT} blocks`);
  console.log(`  disputeWindow     : ${DISPUTE_WINDOW} blocks`);

  const SettlementSC = await hre.ethers.getContractFactory("SettlementSC");
  const settlement = await SettlementSC.deploy(
    collateralAddr,
    identityAddr,
    deployer.address,
    SETTLEMENT_TIMEOUT,
    DISPUTE_WINDOW
  );
  await settlement.waitForDeployment();
  const settlementAddr = await settlement.getAddress();
  console.log(`\n   SettlementSC deployed to: ${settlementAddr}`);

  // ═══════════════════════════════════════════════════════════════════════════
  // Deploy DeliveryRegistry
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log("Deploying DeliveryRegistry...");
  console.log(`  identitySC        : ${identityAddr}`);
  console.log(`  settlementSC      : ${settlementAddr}`);
  console.log(`  requiredReceipts  : ${REQUIRED_RECEIPTS}`);

  const DeliveryRegistry = await hre.ethers.getContractFactory("DeliveryRegistry");
  const delivery = await DeliveryRegistry.deploy(
    identityAddr,
    settlementAddr,
    deployer.address,
    REQUIRED_RECEIPTS
  );
  await delivery.waitForDeployment();
  const deliveryAddr = await delivery.getAddress();
  console.log(`\n   DeliveryRegistry deployed to: ${deliveryAddr}`);

  // ═══════════════════════════════════════════════════════════════════════════
  // Grant roles
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log("Configuring roles...");

  // Grant SETTLEMENT_ROLE to SettlementSC on CollateralSC
  const CollateralSC = await hre.ethers.getContractAt("CollateralSC", collateralAddr);
  const SETTLEMENT_ROLE = await CollateralSC.SETTLEMENT_ROLE();
  await CollateralSC.grantRole(SETTLEMENT_ROLE, settlementAddr);
  console.log(`   Granted SETTLEMENT_ROLE on CollateralSC to SettlementSC`);

  // Grant DELIVERY_REGISTRY_ROLE to DeliveryRegistry on SettlementSC
  const DELIVERY_REGISTRY_ROLE = await settlement.DELIVERY_REGISTRY_ROLE();
  await settlement.grantRole(DELIVERY_REGISTRY_ROLE, deliveryAddr);
  console.log(`   Granted DELIVERY_REGISTRY_ROLE on SettlementSC to DeliveryRegistry`);

  // Grant MATCH_REGISTRY_ROLE to deployer for testing (would be MatchRegistry in prod)
  const MATCH_REGISTRY_ROLE = await settlement.MATCH_REGISTRY_ROLE();
  await settlement.grantRole(MATCH_REGISTRY_ROLE, deployer.address);
  console.log(`   Granted MATCH_REGISTRY_ROLE on SettlementSC to deployer (for testing)`);

  // ═══════════════════════════════════════════════════════════════════════════
  // Summary
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log("✅  Deployment complete!");
  console.log("\n   Update your .env:");
  console.log(`   SETTLEMENT_SC_ADDRESS=${settlementAddr}`);
  console.log(`   DELIVERY_REGISTRY_ADDRESS=${deliveryAddr}`);
  console.log("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
