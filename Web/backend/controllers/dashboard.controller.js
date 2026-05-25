const telemetryService = require('../services/telemetry.service');
const aiService = require('../services/ai.service');
const blockchainService = require('../services/blockchain.service');
const logger = require('../utils/logger');

const dashboardController = {
  /**
   * GET /api/dashboard/summary
   * Get dashboard summary with all active nodes
   */
  async getSummary(req, res, next) {
    try {
      // Get all active nodes
      const activeNodes = await telemetryService.getActiveNodes();

      // Enrich with AI decisions and alerts
      const nodesWithDetails = await Promise.all(
        activeNodes.map(async (node) => {
          const aiDecision = await aiService.getLatestDecision(node.node_id);

          // Determine status based on last seen timestamp
          const lastSeenSeconds = Date.now() / 1000 - node.last_seen;
          let status = 'ACTIVE';
          if (lastSeenSeconds > 300) {
            status = 'INACTIVE'; // Inactive if no data for 5 minutes
          } else if (lastSeenSeconds > 60) {
            status = 'WARNING'; // Warning if no data for 1 minute
          }

          // Get recent blockchain logs as alerts
          const recentLogs = await blockchainService.getRecentLogs(node.node_id, 5);
          const alerts = recentLogs
            .filter((log) => log.event_type === 'ALERT')
            .map((log) => log.payload);

          return {
            node_id: node.node_id,
            latest_power: node.latest_power,
            latest_voltage: node.latest_voltage,
            latest_current: node.latest_current,
            status,
            last_seen: node.last_seen,
            alerts,
            ai_decision: aiDecision ? aiDecision.decision : 'N/A',
            ai_confidence: aiDecision ? aiDecision.confidence : 0,
          };
        })
      );

      // Calculate summary statistics
      const totalPower = nodesWithDetails.reduce((sum, node) => sum + (node.latest_power || 0), 0);
      const activeCount = nodesWithDetails.filter((n) => n.status === 'ACTIVE').length;
      const warningCount = nodesWithDetails.filter((n) => n.status === 'WARNING').length;
      const inactiveCount = nodesWithDetails.filter((n) => n.status === 'INACTIVE').length;

      res.json({
        success: true,
        data: {
          nodes: nodesWithDetails,
          summary: {
            total_nodes: nodesWithDetails.length,
            active_nodes: activeCount,
            warning_nodes: warningCount,
            inactive_nodes: inactiveCount,
            total_power_w: totalPower,
            total_power_kw: (totalPower / 1000).toFixed(2),
          },
          timestamp: Date.now(),
        },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * GET /api/dashboard/node/:node_id
   * Get detailed dashboard data for a specific node
   */
  async getNodeDashboard(req, res, next) {
    try {
      const { node_id } = req.params;

      // Get latest telemetry
      const latest = await telemetryService.getLatestTelemetry(node_id);
      if (!latest) {
        return res.status(404).json({
          success: false,
          error: {
            message: `No data found for node: ${node_id}`,
          },
        });
      }

      // Get power statistics
      const stats = await telemetryService.getPowerStats(node_id, 24);

      // Get latest AI decision
      const aiDecision = await aiService.getLatestDecision(node_id);

      // Get recent trades
      const trades = await blockchainService.getNodeTrades(node_id, 10);

      // Get recent alerts
      const logs = await blockchainService.getRecentLogs(node_id, 10);
      const alerts = logs.filter((log) => log.event_type === 'ALERT');

      // Determine status
      const lastSeenSeconds = Date.now() / 1000 - latest.timestamp;
      let status = 'ACTIVE';
      if (lastSeenSeconds > 300) status = 'INACTIVE';
      else if (lastSeenSeconds > 60) status = 'WARNING';

      res.json({
        success: true,
        data: {
          node_id,
          latest_power: latest.power,
          latest_voltage: latest.voltage,
          latest_current: latest.current,
          status,
          last_seen: latest.timestamp,
          alerts: alerts.map((a) => a.payload),
          ai_decision: aiDecision ? aiDecision.decision : 'N/A',
          ai_confidence: aiDecision ? aiDecision.confidence : 0,
          statistics: {
            avg_power_24h: stats.avg_power,
            max_power_24h: stats.max_power,
            min_power_24h: stats.min_power,
            total_readings_24h: stats.total_readings,
          },
          recent_trades: trades,
        },
      });
    } catch (error) {
      next(error);
    }
  },
};

module.exports = dashboardController;
