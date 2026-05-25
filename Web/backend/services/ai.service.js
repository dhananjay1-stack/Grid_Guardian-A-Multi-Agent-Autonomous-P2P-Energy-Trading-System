/**
 * AI Decision Engine Service
 *
 * Integrates with the Python AI inference server to provide real AI-powered
 * trading decisions based on the trained CQL model.
 */
const axios = require('axios');
const AIResult = require('../models/aiResult.model');
const logger = require('../utils/logger');

// AI inference server configuration
const AI_SERVER_URL = process.env.AI_SERVER_URL || 'http://127.0.0.1:5050';
const AI_SERVER_TIMEOUT = parseInt(process.env.AI_SERVER_TIMEOUT) || 5000;
const USE_MOCK_FALLBACK = process.env.AI_USE_MOCK_FALLBACK !== 'false';

// Cache for AI server health status
let aiServerHealthy = null;
let lastHealthCheck = 0;
const HEALTH_CHECK_INTERVAL = 30000; // 30 seconds

/**
 * Check if AI inference server is available
 */
async function checkAIServerHealth() {
  const now = Date.now();
  if (aiServerHealthy !== null && now - lastHealthCheck < HEALTH_CHECK_INTERVAL) {
    return aiServerHealthy;
  }

  try {
    const response = await axios.get(`${AI_SERVER_URL}/health`, {
      timeout: 2000,
    });
    aiServerHealthy = response.data.status === 'healthy' && response.data.model_loaded;
    lastHealthCheck = now;
    if (aiServerHealthy) {
      logger.info('AI inference server is healthy');
    }
    return aiServerHealthy;
  } catch (error) {
    aiServerHealthy = false;
    lastHealthCheck = now;
    logger.warn('AI inference server health check failed:', error.message);
    return false;
  }
}

/**
 * Get model information from AI server
 */
async function getModelInfo() {
  try {
    const response = await axios.get(`${AI_SERVER_URL}/model-info`, {
      timeout: AI_SERVER_TIMEOUT,
    });
    return response.data;
  } catch (error) {
    logger.error('Failed to get model info:', error.message);
    return null;
  }
}

const aiService = {
  /**
   * Prepare features/observation from telemetry data for AI model
   */
  prepareObservation(telemetryData, additionalData = {}) {
    // Map telemetry to observation format expected by AI model
    return {
      // Core telemetry
      voltage: telemetryData.voltage || 230,
      current: telemetryData.current || 0,
      power: telemetryData.power || 0,

      // Battery state
      soc_kwh: additionalData.soc_kwh || 2.0,
      soc_capacity_kwh: additionalData.soc_capacity_kwh || 4.0,

      // Power flows
      pv_gen_kw: additionalData.pv_gen_kw || (telemetryData.power || 0) / 1000,
      load_kw: additionalData.load_kw || additionalData.avg_power_24h / 1000 || 0.8,
      net_kw: additionalData.net_kw || 0,
      battery_power_kw: additionalData.battery_power_kw || 0,

      // Price and forecast
      price_signal: additionalData.grid_price || 6.0,
      forecast_irradiance_1h: additionalData.forecast_irradiance_1h || 400,
      forecast_irradiance_3h: additionalData.forecast_irradiance_3h || 350,
      forecast_temp_1h: additionalData.forecast_temp_1h || 25,
      actual_irradiance_wm2: additionalData.actual_irradiance_wm2 || 450,

      // Derived voltage/current
      voltage_v: telemetryData.voltage || 230,
      current_a: telemetryData.current || 3.5,
    };
  },

  /**
   * Call AI inference server for decision
   */
  async callAIServer(nodeId, telemetryData, additionalData = {}) {
    const observation = this.prepareObservation(telemetryData, additionalData);

    try {
      const response = await axios.post(
        `${AI_SERVER_URL}/infer`,
        {
          node_id: nodeId,
          telemetry: observation,
          context: additionalData,
          apply_safety: true,
        },
        {
          timeout: AI_SERVER_TIMEOUT,
          headers: {
            'Content-Type': 'application/json',
          },
        }
      );

      return {
        success: true,
        data: response.data,
      };
    } catch (error) {
      logger.error(`AI server inference failed for ${nodeId}:`, error.message);
      return {
        success: false,
        error: error.message,
      };
    }
  },

  /**
   * Mock AI decision (fallback when AI server unavailable)
   */
  mockDecision(features) {
    const startTime = Date.now();

    let decision = 'HOLD';
    let confidence = 0.5;
    let actionKw = 0;
    let tradingDecision = null;

    // Use net power (positive=surplus, negative=deficit) for trading decisions
    const netKw = features.net_kw || 0;
    const socKwh = features.soc_kwh || 2.0;
    const socCapacity = features.soc_capacity_kwh || 4.0;
    const socFraction = socKwh / socCapacity;

    // Rule-based logic using energy balance (INR pricing)
    if (netKw > 0.3) {
      // Surplus energy — sell or charge
      if (socFraction >= 0.5) {
        decision = 'SELL';
        tradingDecision = 'SELL';
        confidence = 0.75 + Math.random() * 0.2;
        actionKw = -netKw;
      } else {
        decision = 'CHARGE';
        confidence = 0.6 + Math.random() * 0.2;
        actionKw = Math.min(netKw, 3.0);
        // Still sell if large surplus and battery not critically low
        if (netKw > 1.0 && socFraction >= 0.3) {
          tradingDecision = 'SELL';
        }
      }
    } else if (netKw < -0.3) {
      // Deficit — buy or discharge
      if (socFraction <= 0.6) {
        decision = 'BUY';
        tradingDecision = 'BUY';
        confidence = 0.7 + Math.random() * 0.2;
        actionKw = Math.abs(netKw);
      } else {
        decision = 'DISCHARGE';
        confidence = 0.65 + Math.random() * 0.2;
        actionKw = -Math.min(Math.abs(netKw), 3.0);
      }
    }

    const executionTime = Date.now() - startTime;

    return {
      decision,
      confidence: Math.min(confidence, 0.99),
      action_kw: actionKw,
      trade_action: tradingDecision,
      execution_time_ms: executionTime,
      is_mock: true,
    };
  },

  /**
   * Process telemetry and generate AI decision
   */
  async processAndDecide(nodeId, telemetryData, additionalData = {}) {
    try {
      const startTime = Date.now();
      let decisionResult;
      let isMock = false;

      // Check if AI server is available
      const serverHealthy = await checkAIServerHealth();

      if (serverHealthy) {
        // Use real AI inference
        const aiResult = await this.callAIServer(nodeId, telemetryData, additionalData);

        if (aiResult.success) {
          decisionResult = {
            decision: aiResult.data.decision,
            confidence: aiResult.data.confidence,
            action_kw: aiResult.data.action_kw,
            action_name: aiResult.data.action_name,
            trade_action: aiResult.data.trade_action,
            recommended_quantity: aiResult.data.recommended_quantity,
            forecasted_load: aiResult.data.forecasted_load,
            forecasted_solar: aiResult.data.forecasted_solar,
            net_power_kw: aiResult.data.net_power_kw,
            model_version: aiResult.data.model_version,
            // Policy selection fields — AI server returns selected_policy, not selected_model
            selected_model: aiResult.data.selected_policy || aiResult.data.selected_model || 'CQL',
            condition: aiResult.data.condition,
            condition_reason: aiResult.data.policy_reason || aiResult.data.condition_reason,
            condition_confidence: aiResult.data.condition_confidence,
            volatility: aiResult.data.volatility,
            sub_conditions: aiResult.data.sub_conditions,
            safety_status: aiResult.data.safety_applied ? 'safe' : 'unchecked',
          };
        } else if (USE_MOCK_FALLBACK) {
          // Fallback to mock
          logger.warn(`Falling back to mock decision for ${nodeId}`);
          const features = this.prepareObservation(telemetryData, additionalData);
          features.avg_power_24h = additionalData.avg_power_24h;
          features.grid_price = additionalData.grid_price;
          decisionResult = this.mockDecision(features);
          isMock = true;
        } else {
          throw new Error('AI server unavailable and mock fallback disabled');
        }
      } else if (USE_MOCK_FALLBACK) {
        // Use mock decision
        logger.debug(`Using mock decision for ${nodeId} (AI server unavailable)`);
        const features = this.prepareObservation(telemetryData, additionalData);
        features.avg_power_24h = additionalData.avg_power_24h;
        features.grid_price = additionalData.grid_price;
        decisionResult = this.mockDecision(features);
        isMock = true;
      } else {
        throw new Error('AI server unavailable');
      }

      const executionTime = Date.now() - startTime;

      // Prepare features for storage
      const features = {
        voltage: telemetryData.voltage || 0,
        current: telemetryData.current || 0,
        power: telemetryData.power || 0,
        avg_power_24h: additionalData.avg_power_24h || 0,
        peak_power: additionalData.peak_power || 0,
        grid_price: additionalData.grid_price || 6.0,
        soc_kwh: additionalData.soc_kwh || 2.0,
        soc_capacity_kwh: additionalData.soc_capacity_kwh || 4.0,
      };

      // Create AI result record
      const aiResult = new AIResult({
        node_id: nodeId,
        decision: decisionResult.decision,
        confidence: decisionResult.confidence,
        features,
        model_version: isMock ? 'mock-v1.0.0' : (decisionResult.model_version || 'GridGuardian-CQL'),
        execution_time_ms: executionTime,
        // Extended fields
        action_kw: decisionResult.action_kw,
        action_name: decisionResult.action_name,
        trade_action: decisionResult.trade_action,
        recommended_quantity: decisionResult.recommended_quantity,
        forecasted_load: decisionResult.forecasted_load,
        forecasted_solar: decisionResult.forecasted_solar,
        net_power_kw: decisionResult.net_power_kw,
        is_mock: isMock,
        // Policy selection fields
        selected_model: isMock ? 'FALLBACK' : decisionResult.selected_model,
        condition: decisionResult.condition,
        condition_reason: decisionResult.condition_reason,
        condition_confidence: decisionResult.condition_confidence,
        volatility: decisionResult.volatility,
        sub_conditions: decisionResult.sub_conditions,
        safety_status: decisionResult.safety_status,
      });

      const saved = await aiResult.save();

      logger.info(
        `AI decision for ${nodeId}: ${decisionResult.decision} ` +
          `(confidence: ${(decisionResult.confidence * 100).toFixed(1)}%, ` +
          `${isMock ? 'mock' : 'real AI'}, ${executionTime}ms)`
      );

      return saved;
    } catch (error) {
      logger.error(`Error processing AI decision for ${nodeId}:`, error);
      throw error;
    }
  },

  /**
   * Get latest AI decision for a node
   */
  async getLatestDecision(nodeId) {
    try {
      return await AIResult.getLatestDecision(nodeId);
    } catch (error) {
      logger.error(`Error getting latest AI decision for ${nodeId}:`, error);
      throw error;
    }
  },

  /**
   * Get AI decision history for a node
   */
  async getDecisionHistory(nodeId, limit = 50) {
    try {
      return await AIResult.find({ node_id: nodeId })
        .sort({ createdAt: -1 })
        .limit(limit)
        .lean();
    } catch (error) {
      logger.error(`Error getting AI decision history for ${nodeId}:`, error);
      throw error;
    }
  },

  /**
   * Get AI server status
   */
  async getServerStatus() {
    const healthy = await checkAIServerHealth();
    let modelInfo = null;

    if (healthy) {
      modelInfo = await getModelInfo();
    }

    return {
      healthy,
      url: AI_SERVER_URL,
      mockFallbackEnabled: USE_MOCK_FALLBACK,
      modelInfo,
    };
  },

  /**
   * Force health check refresh
   */
  async refreshHealthCheck() {
    lastHealthCheck = 0;
    return await checkAIServerHealth();
  },
};

module.exports = aiService;
