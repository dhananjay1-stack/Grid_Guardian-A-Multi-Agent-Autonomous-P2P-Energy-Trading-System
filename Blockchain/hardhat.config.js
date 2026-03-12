require("dotenv").config();
require("@nomicfoundation/hardhat-toolbox");

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.27",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      evmVersion: "cancun",
    },
  },
  networks: {
    localhost: {
      url: process.env.RPC_URL || "http://127.0.0.1:8545",
    },
    hardhat: {
      chainId: 31337,
    },
    // ── Add L2 testnet when ready ──
    // sepolia: {
    //   url: process.env.SEPOLIA_RPC_URL,
    //   accounts: [process.env.PRIVATE_KEY],
    // },
  },
  paths: {
    sources: "./contracts",
    tests: "./tests",
    cache: "./cache",
    artifacts: "./artifacts",
  },
};
