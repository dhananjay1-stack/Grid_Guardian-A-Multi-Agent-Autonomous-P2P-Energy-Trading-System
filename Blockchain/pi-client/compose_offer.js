/**
 * pi-client/compose_offer.js — Deterministic offer composition & hashing
 *
 * Composes a quantized energy trading offer and produces:
 *   - offerHash: keccak256 of the EIP-712 struct hash
 *   - packed offer data for verification
 *
 * Quantization buckets (project-specific):
 *   kwhBucket:   1 = 0.1–0.5 kWh, 2 = 0.5–1.0, 3 = 1.0–2.0, ...
 *   priceBucket: 1 = 1–5 cents, 2 = 5–10 cents, 3 = 10–20 cents, ...
 *
 * Usage:
 *   const { composeOffer } = require('./compose_offer');
 *   const offer = composeOffer(1, nodeIdHex, 2, 3, 0, 1000);
 */
const { ethers } = require("ethers");

// EIP-712 Offer type for struct hashing
const OFFER_TYPEHASH = ethers.keccak256(
  ethers.toUtf8Bytes(
    "Offer(uint32 roundId,bytes32 nodeId,uint16 kwhBucket,uint16 priceBucket,uint32 nonce,uint32 expiryBlock)"
  )
);

/**
 * Compose a deterministic offer and return its EIP-712 struct hash.
 * @param {number} roundId       - Market round integer
 * @param {string} nodeIdHex     - bytes32 node ID from IdentitySC
 * @param {number} kwhBucket     - Quantized kWh bucket (uint16)
 * @param {number} priceBucket   - Quantized price bucket (uint16)
 * @param {number} nonce         - Per-node monotonic nonce (uint32)
 * @param {number} expiryBlock   - Block number after which reveal is rejected (uint32)
 * @returns {{ offerStructHash: string, offerValues: object }}
 */
function composeOffer(roundId, nodeIdHex, kwhBucket, priceBucket, nonce, expiryBlock) {
  const offerValues = {
    roundId,
    nodeId: nodeIdHex,
    kwhBucket,
    priceBucket,
    nonce,
    expiryBlock,
  };

  // EIP-712 struct hash: keccak256(abi.encode(TYPEHASH, ...fields))
  const offerStructHash = ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ["bytes32", "uint32", "bytes32", "uint16", "uint16", "uint32", "uint32"],
      [OFFER_TYPEHASH, roundId, nodeIdHex, kwhBucket, priceBucket, nonce, expiryBlock]
    )
  );

  return { offerStructHash, offerValues };
}

module.exports = { composeOffer, OFFER_TYPEHASH };
