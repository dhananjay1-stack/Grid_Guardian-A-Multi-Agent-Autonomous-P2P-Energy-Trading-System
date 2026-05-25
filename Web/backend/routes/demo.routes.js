/**
 * Demo API Routes
 *
 * Provides REST endpoints for the demo dashboard:
 * - GET /api/demo/state - Get current demo state
 * - POST /api/demo/start - Start the demo
 * - POST /api/demo/stop - Stop the demo
 * - POST /api/demo/step - Run a single step
 * - POST /api/demo/reset - Reset the demo
 */

const express = require('express');
const router = express.Router();
const { getSimulationController } = require('../simulation/simulation_controller');
const logger = require('../utils/logger');

// AI Service URL
const AI_SERVICE_URL = process.env.AI_SERVICE_URL || 'http://localhost:5050';

// Event log for timeline (in-memory, max 100 events)
let eventLog = [];
const MAX_EVENTS = 100;
let eventCounter = 0;

function addEvent(type, title, description, prosumer_id = null, metadata = {}) {
  eventCounter++;
  const event = {
    id: `evt-${eventCounter}`,
    type,
    timestamp: Date.now(),
    title,
    description,
    prosumer_id,
    metadata,
  };
  eventLog.push(event);
  if (eventLog.length > MAX_EVENTS) {
    eventLog.shift();
  }
  return event;
}

// Call real AI inference server
async function getAIDecision(prosumer, sim = null) {
  try {
    const marketPrice = sim?.market?.current_price || 6.0;
    const solarCap = prosumer.config?.solar_capacity_kw || 1;
    const currentHour = prosumer.current_hour || 12;

    // Calculate forecast irradiance from prosumer's solar profile
    const forecastIrr1h = prosumer.getSolarGeneration
      ? prosumer.getSolarGeneration((currentHour + 1) % 24) / solarCap * 1000
      : 400;
    const forecastIrr3h = prosumer.getSolarGeneration
      ? prosumer.getSolarGeneration((currentHour + 3) % 24) / solarCap * 1000
      : 350;
    const actualIrradiance = prosumer.solar_generation_kw
      ? prosumer.solar_generation_kw / solarCap * 1000
      : 400;

    const response = await fetch(`${AI_SERVICE_URL}/infer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        node_id: prosumer.prosumer_id,
        telemetry: {
          soc_kwh: prosumer.battery?.soc_kwh || 2.0,
          soc_capacity_kwh: prosumer.battery?.capacity_kwh || 5.0,
          pv_gen_kw: prosumer.solar_generation_kw || 0,
          load_kw: prosumer.load_demand_kw || 0.5,
          net_kw: prosumer.net_power_kw || 0,
          battery_power_kw: prosumer.battery?.current_power_kw || 0,
          voltage_v: 230 + Math.random() * 10 - 5,
          current_a: Math.abs(prosumer.net_power_kw || 0) * 1000 / 230,
          price_signal: marketPrice,
          forecast_irradiance_1h: forecastIrr1h,
          forecast_irradiance_3h: forecastIrr3h,
          forecast_temp_1h: 25 + Math.random() * 10 - 5,
          actual_irradiance_wm2: actualIrradiance,
          neighbor_balance: 0.0,
        },
        use_dynamic_selection: true,
      }),
      signal: AbortSignal.timeout(3000), // 3 second timeout
    });

    if (response.ok) {
      return await response.json();
    }
  } catch (error) {
    logger.warn(`AI service call failed for ${prosumer.prosumer_id}: ${error.message}`);
  }
  return null;
}

// Get simulation controller instance
let simulation = null;
let aiServiceAvailable = null;

const getSimulation = () => {
  if (!simulation) {
    simulation = getSimulationController({
      timestep_minutes: 5,
      simulation_speed: 60,
      auto_matching: true,
      auto_settlement: true,
    });
    simulation.initialize();

    // Listen for simulation events
    simulation.on('trade_settled', (trade) => {
      addEvent(
        'settlement',
        'Trade Settled',
        `${trade.seller_id} → ${trade.buyer_id}: ${trade.quantity_kwh.toFixed(2)} kWh`,
        null,
        { tx: trade.tx_hash?.substring(0, 10) + '...', quantity: trade.quantity_kwh }
      );
    });
  }
  return simulation;
};

// Check AI service availability
async function checkAIService() {
  try {
    const response = await fetch(`${AI_SERVICE_URL}/health`, {
      signal: AbortSignal.timeout(2000),
    });
    if (response.ok) {
      const data = await response.json();
      aiServiceAvailable = data.model_loaded || false;
      return aiServiceAvailable;
    }
  } catch (e) {
    aiServiceAvailable = false;
  }
  return false;
}

// Track last tick we generated events for (avoid duplicate events per poll)
let lastEventTick = -1;

/**
 * GET /api/demo/state
 * Get current demo state — uses CACHED AI decisions from simulation tick
 * (single source of truth: what the dashboard shows = what drove the trade)
 */
router.get('/state', async (req, res) => {
  try {
    const sim = getSimulation();
    const state = sim.getState();
    const cachedDecisions = state.cached_ai_decisions || {};
    const currentTick = state.tick_count || 0;
    const isNewTick = currentTick !== lastEventTick;

    // Check AI service on first call or periodically
    if (aiServiceAvailable === null) {
      await checkAIService();
    }

    // Build all settled trades from recent_trades for blockchain panel
    const allSettledTrades = (state.recent_trades || []).filter(t => t.status === 'settled');
    const totalSettlements = state.market?.stats?.total_matches || allSettledTrades.length;
    const totalValue = state.market?.stats?.total_value || 0;

    // Transform to demo format
    const demoState = {
      tick: currentTick,
      hour: state.simulation_hour,
      day: state.simulation_day,
      is_running: state.is_running,
      prosumers: {},
      ai_decisions: {},
      trades: state.recent_trades || [],
      market: {
        ...(state.market || {}),
        open_offers: state.order_book?.offers?.length || state.market?.open_offers || 0,
        open_bids: state.order_book?.bids?.length || state.market?.open_bids || 0,
        total_matches: state.market?.stats?.total_matches || 0,
        total_volume_kwh: state.market?.stats?.total_volume_kwh || 0,
        total_value: totalValue,
      },
      events: eventLog.slice(-30),
      edge_status: {
        is_connected: true,
        mode: 'simulation',
        runtime_status: state.is_running ? 'running' : 'stopped',
        sensor_status: 'ok',
        relay_state: 'off',
        telemetry_status: state.is_running ? 'streaming' : 'buffered',
        device_info: {
          device_id: 'sim-001',
          firmware_version: '2.1.0',
          uptime_seconds: currentTick * 300,
        },
      },
      blockchain_status: {
        is_connected: true,
        network: 'Hardhat Local',
        block_number: 12345 + currentTick,
        pending_trades: state.market?.pending_trades || 0,
        total_settlements: totalSettlements,
        total_value: totalValue,
        recent_settlements: allSettledTrades.slice(-10).map(t => ({
          trade_id: t.trade_id,
          tx_hash: t.blockchain_tx_hash,
          status: 'settled',
          seller_id: t.seller_id,
          buyer_id: t.buyer_id,
          quantity_kwh: t.quantity_kwh,
          price_per_kwh: t.price_per_kwh,
          total_price: t.total_price,
          timestamp: t.settled_at || t.matched_at || Date.now(),
        })),
      },
      ai_service_status: aiServiceAvailable,
    };

    // Transform prosumer states
    for (const prosumer of state.prosumers || []) {
      const prosumerId = prosumer.prosumer_id;

      demoState.prosumers[prosumerId] = {
        prosumer_id: prosumerId,
        name: prosumer.name,
        solar_kw: prosumer.solar_generation_kw || 0,
        load_kw: prosumer.load_demand_kw || 0,
        net_power_kw: prosumer.net_power_kw || 0,
        surplus_kw: Math.max(0, prosumer.net_power_kw || 0),
        deficit_kw: Math.abs(Math.min(0, prosumer.net_power_kw || 0)),
        battery: {
          soc_kwh: prosumer.battery?.soc_kwh || 0,
          soc_fraction: (prosumer.battery?.soc_percent || 50) / 100,
          capacity_kwh: prosumer.battery?.capacity_kwh || 5,
        },
        trade_status: prosumer.trade_status || 'idle',
      };

      // Use CACHED decision from the simulation tick (single source of truth)
      const cached = cachedDecisions[prosumerId];

      if (cached && cached.action) {
        // Build the AI decision from the cached tick decision
        demoState.ai_decisions[prosumerId] = {
          action_name: cached.action_name || cached.action || 'idle',
          action_kw: cached.action_kw || 0,
          decision: cached.action === 'sell_surplus' ? 'SELL' :
                    cached.action === 'buy_energy' ? 'BUY' :
                    cached.action === 'charge_battery' ? 'CHARGE' :
                    cached.action === 'discharge_battery' ? 'DISCHARGE' :
                    (cached.action || 'HOLD').toUpperCase(),
          trade_action: cached.trade_action || null,
          confidence: cached.confidence || 0.5,
          selected_policy: cached.selected_policy || 'FALLBACK',
          policy_reason: cached.policy_reason || '',
          condition: cached.condition || 'normal',
          condition_confidence: cached.condition_confidence || 0.7,
          volatility: cached.volatility || 0,
          sub_conditions: cached.sub_conditions || [],
          supplementary_override: cached.supplementary_override || false,
          source: cached.source || 'unknown',
        };

        // Update prosumer trade_status to match the decision that drove the actual trade
        const tradeAction = cached.trade_action;
        if (tradeAction === 'SELL') {
          demoState.prosumers[prosumerId].trade_status = 'offering';
        } else if (tradeAction === 'BUY') {
          demoState.prosumers[prosumerId].trade_status = 'bidding';
        }

        // Mark AI service available based on source
        if (cached.source === 'ai') {
          aiServiceAvailable = true;
        }

        // Generate events only once per tick (not per poll)
        if (state.is_running && isNewTick) {
          addEvent(
            'sensing',
            'Sensor Reading',
            `${prosumer.name}: Solar ${(prosumer.solar_generation_kw || 0).toFixed(1)}kW, Load ${(prosumer.load_demand_kw || 0).toFixed(1)}kW, Net ${(prosumer.net_power_kw || 0).toFixed(1)}kW`,
            prosumerId,
            { solar: prosumer.solar_generation_kw, load: prosumer.load_demand_kw }
          );

          addEvent(
            'condition',
            'Condition Detected',
            `${cached.condition || 'normal'} (confidence: ${((cached.condition_confidence || 0.7) * 100).toFixed(0)}%)`,
            prosumerId,
            { condition: cached.condition }
          );

          addEvent(
            'model_selection',
            'AI Model Selected',
            `${cached.selected_policy || 'FALLBACK'}: ${cached.policy_reason || 'Decision made'}`,
            prosumerId,
            { model: cached.selected_policy }
          );

          const actionLabel = cached.action_name || cached.action || 'idle';
          const tradeLabel = cached.trade_action ? ` → Trade: ${cached.trade_action}` : '';
          addEvent(
            'action',
            'AI Action',
            `${actionLabel.replace(/_/g, ' ')}${tradeLabel} (${((cached.confidence || 0.5) * 100).toFixed(0)}% confidence)`,
            prosumerId,
            { action: actionLabel, trade_action: cached.trade_action, confidence: cached.confidence }
          );
        }
      } else {
        // No cached decision yet (before first tick) — use live AI call or fallback
        const realProsumer = sim.prosumers.get(prosumerId) || prosumer;
        const aiResult = await getAIDecision(realProsumer, sim);

        if (aiResult && aiResult.action_name) {
          demoState.ai_decisions[prosumerId] = {
            action_name: aiResult.action_name,
            action_kw: aiResult.action_kw || 0,
            decision: aiResult.decision || 'HOLD',
            trade_action: aiResult.trade_action || null,
            confidence: aiResult.confidence || 0.5,
            selected_policy: aiResult.selected_policy || 'CQL',
            policy_reason: aiResult.policy_reason || 'AI inference (pre-tick)',
            condition: aiResult.condition || 'normal',
            condition_confidence: aiResult.condition_confidence || 0.8,
            volatility: aiResult.volatility || 0.1,
            sub_conditions: aiResult.sub_conditions || [],
            source: 'ai',
          };
          aiServiceAvailable = true;
        } else {
          // Rule-based fallback
          const netPower = prosumer.net_power_kw || 0;
          const socPercent = prosumer.battery?.soc_percent || 50;
          let action, decision, condition, tradeAction;

          if (netPower > 0.3 && socPercent > 40) {
            action = 'offer_sell'; decision = 'SELL'; condition = 'high_pv'; tradeAction = 'SELL';
          } else if (netPower < -0.3 && socPercent < 70) {
            action = 'buy_energy'; decision = 'BUY';
            condition = socPercent < 30 ? 'low_soc' : 'high_load'; tradeAction = 'BUY';
          } else if (netPower > 0.1) {
            action = 'charge_small'; decision = 'CHARGE'; condition = 'normal'; tradeAction = null;
          } else if (netPower < -0.1 && socPercent > 20) {
            action = 'discharge_small'; decision = 'DISCHARGE'; condition = 'normal'; tradeAction = null;
          } else {
            action = 'idle'; decision = 'HOLD'; condition = 'normal'; tradeAction = null;
          }

          demoState.ai_decisions[prosumerId] = {
            action_name: action, action_kw: 0, decision, trade_action: tradeAction,
            confidence: 0.6, selected_policy: 'FALLBACK',
            policy_reason: 'No cached decision - rule-based fallback',
            condition, condition_confidence: 0.5, volatility: 0.1, sub_conditions: [],
            source: 'rule_based',
          };

          if (tradeAction === 'SELL') demoState.prosumers[prosumerId].trade_status = 'offering';
          else if (tradeAction === 'BUY') demoState.prosumers[prosumerId].trade_status = 'bidding';

          aiServiceAvailable = false;
        }
      }
    }

    // Add trade events for new trades (only once per tick)
    if (isNewTick) {
      for (const trade of state.recent_trades || []) {
        if (trade.status === 'settled' && !trade._eventAdded) {
          addEvent(
            'trade',
            'Trade Settled',
            `${trade.seller_name || trade.seller_id} → ${trade.buyer_name || trade.buyer_id}: ${trade.quantity_kwh.toFixed(3)} kWh @ ₹${trade.price_per_kwh.toFixed(2)}/kWh = ₹${trade.total_price.toFixed(2)}`,
            null,
            { quantity: trade.quantity_kwh, price: trade.price_per_kwh, tx_hash: trade.blockchain_tx_hash }
          );
          trade._eventAdded = true;
        }
      }
      lastEventTick = currentTick;
    }

    res.json(demoState);
  } catch (error) {
    logger.error('Demo state error:', error);
    res.status(500).json({ error: error.message });
  }
});

/**
 * POST /api/demo/start
 * Start the demo simulation
 */
router.post('/start', async (req, res) => {
  try {
    const sim = getSimulation();
    const speed = req.body?.speed || 60;
    sim.setSpeed(speed);

    // Check AI service before starting
    await checkAIService();

    const result = sim.start();

    if (result.success) {
      logger.info(`Demo started (AI service: ${aiServiceAvailable ? 'connected' : 'fallback mode'})`);
      addEvent('edge', 'Demo Started', `Simulation running at ${speed}x speed`, null, { speed });
      res.json({
        success: true,
        message: 'Demo started',
        ai_service: aiServiceAvailable ? 'connected' : 'fallback'
      });
    } else {
      res.json({ success: false, reason: result.reason });
    }
  } catch (error) {
    logger.error('Demo start error:', error);
    res.status(500).json({ error: error.message });
  }
});

/**
 * POST /api/demo/stop
 * Stop the demo simulation
 */
router.post('/stop', (req, res) => {
  try {
    const sim = getSimulation();
    const result = sim.stop();

    if (result.success) {
      logger.info('Demo stopped');
      addEvent('edge', 'Demo Stopped', 'Simulation paused');
      res.json({ success: true, message: 'Demo stopped' });
    } else {
      res.json({ success: false, reason: result.reason });
    }
  } catch (error) {
    logger.error('Demo stop error:', error);
    res.status(500).json({ error: error.message });
  }
});

/**
 * POST /api/demo/step
 * Run a single simulation step
 */
router.post('/step', async (req, res) => {
  try {
    const sim = getSimulation();
    const result = await sim.step();

    logger.info(`Demo stepped to tick ${result.tick}`);
    addEvent('edge', 'Manual Step', `Stepped to tick ${result.tick}`);
    res.json({ success: true, tick: result.tick });
  } catch (error) {
    logger.error('Demo step error:', error);
    res.status(500).json({ error: error.message });
  }
});

/**
 * POST /api/demo/reset
 * Reset the demo simulation
 */
router.post('/reset', (req, res) => {
  try {
    const sim = getSimulation();
    sim.reset();
    sim.initialize();

    // Clear event log
    eventLog = [];
    eventCounter = 0;

    logger.info('Demo reset');
    addEvent('edge', 'Demo Reset', 'Simulation reset to initial state');
    res.json({ success: true, message: 'Demo reset' });
  } catch (error) {
    logger.error('Demo reset error:', error);
    res.status(500).json({ error: error.message });
  }
});

/**
 * POST /api/demo/set-hour
 * Set simulation hour
 */
router.post('/set-hour', (req, res) => {
  try {
    const sim = getSimulation();
    const hour = req.body?.hour || 12;
    const result = sim.setHour(hour);

    res.json({ success: true, hour: result.hour });
  } catch (error) {
    logger.error('Demo set-hour error:', error);
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/demo/ai-status
 * Check AI service status
 */
router.get('/ai-status', async (req, res) => {
  try {
    const available = await checkAIService();
    res.json({
      available,
      url: AI_SERVICE_URL,
      message: available ? 'AI inference server connected with trained models' : 'AI service unavailable - using fallback rules',
    });
  } catch (error) {
    res.json({ available: false, error: error.message });
  }
});

module.exports = router;
