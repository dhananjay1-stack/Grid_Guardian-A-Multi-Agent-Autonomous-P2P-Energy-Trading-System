const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("-----------------------------------------");
  console.log("DEPLOYMENT START");

  // Deploy Identity
  const IdentitySC = await hre.ethers.getContractFactory("IdentitySC");
  const identity = await IdentitySC.deploy(deployer.address);
  await identity.waitForDeployment();
  const identityAddress = await identity.getAddress();

  // Grant relayer role to deployer
  const RELAYER_ROLE = hre.ethers.id("RELAYER_ROLE");
  await identity.grantRole(RELAYER_ROLE, deployer.address);

  // Deploy Stablecoin
  const MockUSDC = await hre.ethers.getContractFactory("MockUSDC");
  const usdc = await MockUSDC.deploy(deployer.address);
  await usdc.waitForDeployment();
  const usdcAddress = await usdc.getAddress();

  // Deploy Collateral
  const CollateralSC = await hre.ethers.getContractFactory("CollateralSC");
  const collateral = await CollateralSC.deploy(usdcAddress, identityAddress, deployer.address);
  await collateral.waitForDeployment();
  const collateralAddress = await collateral.getAddress();

  // Deploy MatchRegistry
  const MatchRegistry = await hre.ethers.getContractFactory("MatchRegistry");
  const matchRegistry = await MatchRegistry.deploy(usdcAddress, deployer.address, 10, 1000, 100, 1);
  await matchRegistry.waitForDeployment();
  const matchAddress = await matchRegistry.getAddress();

  // Deploy TradingSC
  const TradingSC = await hre.ethers.getContractFactory("TradingSC");
  const trading = await TradingSC.deploy(identityAddress);
  await trading.waitForDeployment();
  const tradingAddress = await trading.getAddress();
  
  // Deploy SettlementSC
  const SettlementSC = await hre.ethers.getContractFactory("SettlementSC");
  // constructor(_collateralSC, _identitySC, admin, _settlementTimeout, _disputeWindow)
  const settlement = await SettlementSC.deploy(collateralAddress, identityAddress, deployer.address, 10, 10);
  await settlement.waitForDeployment();
  const settlementAddress = await settlement.getAddress();

  // Deploy DeliveryRegistry
  const DeliveryRegistry = await hre.ethers.getContractFactory("DeliveryRegistry");
  // constructor(_identitySC, _settlementSC, admin, _requiredReceipts)
  const delivery = await DeliveryRegistry.deploy(identityAddress, settlementAddress, deployer.address, 1);
  await delivery.waitForDeployment();
  const deliveryAddress = await delivery.getAddress();

  console.log("-----------------------------------------");
  console.log("ENVIRONMENT VARIABLES:");
  console.log(`IDENTITY_SC_ADDRESS=${identityAddress}`);
  console.log(`COLLATERAL_SC_ADDRESS=${collateralAddress}`);
  console.log(`TRADING_SC_ADDRESS=${tradingAddress}`);
  console.log(`MATCH_REGISTRY_ADDRESS=${matchAddress}`);
  console.log(`SETTLEMENT_SC_ADDRESS=${settlementAddress}`);
  console.log(`DELIVERY_REGISTRY_ADDRESS=${deliveryAddress}`);
  console.log("-----------------------------------------");
}

main().catch(console.error);
