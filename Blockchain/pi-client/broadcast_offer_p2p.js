/**
 * pi-client/broadcast_offer_p2p.js — Off-chain P2P offer broadcast (dev stub)
 *
 * In production, this would use libp2p or MQTT to broadcast the full offer
 * (without salt) to local cluster participants for matching.
 *
 * For dev/testing, this logs the offer to console and optionally writes
 * it to a local gossip file for the simulator to pick up.
 *
 * Usage:
 *   const { broadcastOfferP2P } = require('./broadcast_offer_p2p');
 *   await broadcastOfferP2P(offerJson, { gossipDir: './gossip' });
 */
const fs   = require("fs");
const path = require("path");

/**
 * Broadcast an offer off-chain (dev stub).
 * @param {object} offerJson - Full offer payload (without salt)
 *   { roundId, nodeId, kwhBucket, priceBucket, nonce, expiryBlock, offerHash, commitHash, signature, signer }
 * @param {object} [options]
 * @param {string} [options.gossipDir] - Directory to write gossip files (optional)
 */
async function broadcastOfferP2P(offerJson, options = {}) {
  console.log("[P2P] Broadcasting offer off-chain (dev):");
  console.log(`  roundId     : ${offerJson.roundId}`);
  console.log(`  nodeId      : ${offerJson.nodeId}`);
  console.log(`  kwhBucket   : ${offerJson.kwhBucket}`);
  console.log(`  priceBucket : ${offerJson.priceBucket}`);
  console.log(`  nonce       : ${offerJson.nonce}`);
  console.log(`  expiryBlock : ${offerJson.expiryBlock}`);
  console.log(`  offerHash   : ${offerJson.offerHash}`);
  console.log(`  commitHash  : ${offerJson.commitHash}`);
  console.log(`  signer      : ${offerJson.signer}`);

  // ── Optional: write to local gossip directory for simulators ──
  if (options.gossipDir) {
    if (!fs.existsSync(options.gossipDir)) {
      fs.mkdirSync(options.gossipDir, { recursive: true });
    }
    const filename = `offer_${offerJson.commitHash.slice(2, 10)}_${Date.now()}.json`;
    const filepath = path.join(options.gossipDir, filename);
    fs.writeFileSync(filepath, JSON.stringify(offerJson, null, 2));
    console.log(`  Written to: ${filepath}`);
  }

  // ── Production: use libp2p or MQTT ──
  // const { createLibp2p } = require('libp2p');
  // ... or:
  // const mqtt = require('mqtt');
  // const client = mqtt.connect(options.mqttUrl);
  // client.publish('gridguardian/offers', JSON.stringify(offerJson));
}

module.exports = { broadcastOfferP2P };
