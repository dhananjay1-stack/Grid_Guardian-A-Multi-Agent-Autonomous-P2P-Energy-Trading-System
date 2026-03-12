/**
 * pi-client/sign_offer.js — EIP-712 typed signing of an Offer struct
 *
 * Signs the offer using EIP-712 typed data, producing a signature
 * that the TradingSC contract can verify via _hashTypedDataV4 + ECDSA.recover.
 *
 * Usage:
 *   const { signOffer } = require('./sign_offer');
 *   const sig = await signOffer(wallet, contractAddress, chainId, offerValues);
 */
const { ethers } = require("ethers");

// EIP-712 types for the Offer struct (must match TradingSC.OFFER_TYPEHASH)
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

/**
 * Build the EIP-712 domain for TradingSC.
 * @param {string} verifyingContract - TradingSC deployed address
 * @param {number} chainId           - Chain ID (31337 for Hardhat)
 */
function getTradingDomain(verifyingContract, chainId = 31337) {
  return {
    name:              "GridGuardian-Trading",
    version:           "1",
    chainId,
    verifyingContract,
  };
}

/**
 * Sign an Offer struct using EIP-712 typed data.
 * @param {ethers.Wallet} wallet          - The node's wallet
 * @param {string}        contractAddress - TradingSC deployed address
 * @param {number}        chainId         - Chain ID
 * @param {object}        offerValues     - { roundId, nodeId, kwhBucket, priceBucket, nonce, expiryBlock }
 * @returns {Promise<string>} EIP-712 signature
 */
async function signOffer(wallet, contractAddress, chainId, offerValues) {
  const domain = getTradingDomain(contractAddress, chainId);
  const signature = await wallet.signTypedData(domain, OFFER_TYPES, offerValues);
  return signature;
}

module.exports = { signOffer, getTradingDomain, OFFER_TYPES };
