import React, { useState, useEffect, useCallback } from 'react';
import socketService from './services/socket';
import NodeStatusCard from './components/NodeStatusCard';
import AIDecisionCard from './components/AIDecisionCard';
import BatteryCard from './components/BatteryCard';
import TradeListCard from './components/TradeListCard';
import BlockchainStatusCard from './components/BlockchainStatusCard';
import TelemetryChart from './components/TelemetryChart';

function App() {
  const [connected, setConnected] = useState(false);
  const [nodeStatus, setNodeStatus] = useState({
    node_id: 'edge-node-01',
    status: 'offline',
    voltage: 0,
    current: 0,
    power: 0,
    condition: 'normal',
    uptime: 0,
  });
  const [aiDecision, setAiDecision] = useState({
    decision: 'HOLD',
    confidence: 0.5,
    action_name: 'idle',
    action_kw: 0,
    model_key: 'cql_policy',
    condition: 'normal',
    is_mock: true,
  });
  const [batteryState, setBatteryState] = useState({
    soc: 50,
    soc_kwh: 2.0,
    capacity_kwh: 4.0,
    is_charging: false,
    is_discharging: false,
    power_kw: 0,
  });
  const [trades, setTrades] = useState([]);
  const [blockchainStatus, setBlockchainStatus] = useState({
    connected: false,
    block_number: 0,
    pending_trades: 0,
    settlements_today: 0,
  });
  const [telemetryHistory, setTelemetryHistory] = useState([]);

  // Socket event handlers
  const handleTelemetry = useCallback((data) => {
    setNodeStatus((prev) => ({
      ...prev,
      status: 'online',
      voltage: data.voltage || prev.voltage,
      current: data.current || prev.current,
      power: data.power || prev.power,
      condition: data.condition || prev.condition,
    }));

    // Update battery if available
    if (data.battery_soc !== undefined) {
      setBatteryState((prev) => ({
        ...prev,
        soc: data.battery_soc * 100,
        soc_kwh: data.soc_kwh || prev.soc_kwh,
        power_kw: data.battery_power_kw || 0,
        is_charging: (data.battery_power_kw || 0) > 0,
        is_discharging: (data.battery_power_kw || 0) < 0,
      }));
    }

    // Add to history
    setTelemetryHistory((prev) => {
      const newPoint = {
        time: new Date().toLocaleTimeString(),
        power: data.power || 0,
        voltage: data.voltage || 230,
        soc: (data.battery_soc || 0.5) * 100,
      };
      const updated = [...prev, newPoint];
      return updated.slice(-30); // Keep last 30 points
    });
  }, []);

  const handleAiDecision = useCallback((data) => {
    setAiDecision({
      decision: data.decision || 'HOLD',
      confidence: data.confidence || 0.5,
      action_name: data.action_name || 'idle',
      action_kw: data.action_kw || 0,
      trade_action: data.trade_action,
      recommended_quantity: data.recommended_quantity,
      model_key: data.model_key || 'cql_policy',
      condition: data.condition || 'normal',
      is_mock: data.is_mock || false,
    });
  }, []);

  const handleTradeProposal = useCallback((data) => {
    setTrades((prev) => {
      // Avoid duplicates
      if (prev.some((t) => t.trade_id === data.trade_id)) {
        return prev;
      }
      return [data, ...prev].slice(0, 20);
    });
  }, []);

  const handleTradeUpdate = useCallback((data) => {
    setTrades((prev) =>
      prev.map((t) =>
        t.trade_id === data.trade_id ? { ...t, ...data } : t
      )
    );
  }, []);

  // Initialize socket connection
  useEffect(() => {
    socketService.connect();

    socketService.on('connect', () => {
      setConnected(true);
      // Join node room
      socketService.emit('join', 'edge-node-01');
    });

    socketService.on('disconnect', () => {
      setConnected(false);
      setNodeStatus((prev) => ({ ...prev, status: 'offline' }));
    });

    socketService.on('telemetry', handleTelemetry);
    socketService.on('ai:decision', handleAiDecision);
    socketService.on('ai:trade_proposal', handleTradeProposal);
    socketService.on('ai:trade_executed', handleTradeUpdate);
    socketService.on('ai:trade_cancelled', handleTradeUpdate);

    // Fetch initial data
    fetchInitialData();

    return () => {
      socketService.disconnect();
    };
  }, [handleTelemetry, handleAiDecision, handleTradeProposal, handleTradeUpdate]);

  const fetchInitialData = async () => {
    try {
      // Fetch AI status
      const aiStatus = await fetch('/api/ai/status').then((r) => r.json());
      if (aiStatus.success) {
        setBlockchainStatus((prev) => ({
          ...prev,
          connected: aiStatus.data.healthy,
          pending_trades: aiStatus.data.trading_integration?.pending_trades?.total_pending || 0,
        }));
      }

      // Fetch latest telemetry
      const telemetry = await fetch('/api/telemetry/edge-node-01/latest').then((r) => r.json());
      if (telemetry.success && telemetry.data) {
        handleTelemetry(telemetry.data);
      }

      // Fetch latest AI decision
      const decision = await fetch('/api/ai/decision/edge-node-01').then((r) => r.json());
      if (decision.success && decision.data) {
        handleAiDecision(decision.data);
      }
    } catch (error) {
      console.error('Error fetching initial data:', error);
    }
  };

  return (
    <div className="app">
      <header className="header">
        <h1>Grid-Guardian Dashboard</h1>
        <div className={`connection-status ${connected ? 'connected' : 'disconnected'}`}>
          <span className={`status-dot ${connected ? 'connected' : 'disconnected'}`}></span>
          {connected ? 'Connected' : 'Disconnected'}
        </div>
      </header>

      <div className="dashboard-grid">
        <NodeStatusCard status={nodeStatus} />
        <AIDecisionCard decision={aiDecision} />
        <BatteryCard battery={batteryState} />
        <TradeListCard trades={trades} />
        <BlockchainStatusCard status={blockchainStatus} />
        <TelemetryChart data={telemetryHistory} />
      </div>
    </div>
  );
}

export default App;
