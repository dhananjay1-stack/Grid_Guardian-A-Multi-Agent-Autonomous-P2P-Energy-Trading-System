/**
 * Simulation Routes
 *
 * API routes for the virtual P2P trading simulation.
 */

const express = require('express');
const router = express.Router();
const simulationController = require('../controllers/simulation.controller');

// ========================================
// Simulation Control
// ========================================

// Initialize simulation
router.post('/initialize', simulationController.initialize);

// Start simulation
router.post('/start', simulationController.start);

// Stop simulation
router.post('/stop', simulationController.stop);

// Run single step
router.post('/step', simulationController.step);

// Reset simulation
router.post('/reset', simulationController.reset);

// Get simulation state
router.get('/state', simulationController.getState);

// Set speed
router.post('/speed', simulationController.setSpeed);

// Set time
router.post('/time', simulationController.setTime);

// ========================================
// Prosumer Management
// ========================================

// Get all prosumers
router.get('/prosumers', simulationController.getProsumers);

// Get single prosumer
router.get('/prosumers/:id', simulationController.getProsumer);

// Add prosumer
router.post('/prosumers', simulationController.addProsumer);

// Remove prosumer
router.delete('/prosumers/:id', simulationController.removeProsumer);

// ========================================
// Market Operations
// ========================================

// Get market state
router.get('/market', simulationController.getMarket);

// Get order book
router.get('/market/orderbook', simulationController.getOrderBook);

// Get recent trades
router.get('/market/trades', simulationController.getTrades);

// Submit offer
router.post('/market/offer', simulationController.submitOffer);

// Submit bid
router.post('/market/bid', simulationController.submitBid);

// Cancel order
router.delete('/market/order/:id', simulationController.cancelOrder);

// Run matching
router.post('/market/match', simulationController.runMatching);

module.exports = router;
