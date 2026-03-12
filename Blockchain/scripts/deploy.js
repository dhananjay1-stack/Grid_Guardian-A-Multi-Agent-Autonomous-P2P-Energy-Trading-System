/**
 * scripts/deploy.js — Deploy IdentitySC to the local Hardhat network.
 *
 * Usage:
 *   npx hardhat node                                     (terminal 1)
 *   npx hardhat run scripts/deploy.js --network localhost (terminal 2)
 *
 * After deploy the contract address is printed. Copy it to .env IDENTITY_SC_ADDRESS.
 */
require("dotenv").config();
const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying IdentitySC with account:", deployer.address);

  const IdentitySC = await hre.ethers.getContractFactory("IdentitySC");
  const identity = await IdentitySC.deploy(deployer.address);
  await identity.waitForDeployment();

  const address = await identity.getAddress();
  console.log("IdentitySC deployed to:", address);

  // ── Grant RELAYER_ROLE to the relayer account ──
  const relayerKey = process.env.RELAYER_PRIVATE_KEY;
  if (relayerKey) {
    const relayerWallet = new hre.ethers.Wallet(relayerKey, hre.ethers.provider);
    const RELAYER_ROLE = hre.ethers.keccak256(hre.ethers.toUtf8Bytes("RELAYER_ROLE"));
    const tx = await identity.grantRole(RELAYER_ROLE, relayerWallet.address);
    await tx.wait();
    console.log("RELAYER_ROLE granted to:", relayerWallet.address);
  }

  console.log("\n✅  Update your .env:");
  console.log(`   IDENTITY_SC_ADDRESS=${address}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
