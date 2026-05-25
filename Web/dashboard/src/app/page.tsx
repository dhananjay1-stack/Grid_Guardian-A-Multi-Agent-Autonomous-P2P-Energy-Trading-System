'use client';

import { useEffect, useState, useCallback } from 'react';
import {
  useTelemetryStore,
  useAIStore,
  useBlockchainStore,
  useSystemStore,
} from '@/store';
import {
  telemetryService,
  aiService,
  blockchainService,
  realtimeService,
} from '@/services';
import api from '@/services/api';
import {
  TelemetryCard,
  TelemetryChart,
  AIDecisionCard,
  ForecastPanel,
  TradePanel,
  ControlPanel,
  HealthStatusCard,
  LiveConnectionBadge,
  SummaryStrip,
} from '@/components/dashboard';
import { Zap, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SystemHealth, DashboardSummary, TelemetryData, AIDecisionData, Trade } from '@/types';

// Default node for demo
const DEFAULT_NODE = 'pi-001';

export default function DashboardPage() {
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [selectedNode, setSelectedNode] = useState(DEFAULT_NODE);

  // Stores
  const {
    latestByNode,
    historyByNode,
    selectedPeriod,
    isLoading: telemetryLoading,
    setLatestTelemetry,
    setHistory,
    setLoading: setTelemetryLoading,
  } = useTelemetryStore();

  const {
    decisionsByNode,
    isLoading: aiLoading,
    setDecision,
    setLoading: setAILoading,
  } = useAIStore();

  const {
    trades,
    activeTrades,
    isLoading: blockchainLoading,
    setTrades,
    setLoading: setBlockchainLoading,
  } = useBlockchainStore();

  const {
    health,
    summary,
    setHealth,
    setSummary,
    setLoading: setSystemLoading,
  } = useSystemStore();

  // Fetch initial data
  const fetchInitialData = useCallback(async () => {
    setIsInitialLoading(true);

    try {
      // Fetch system health
      try {
        const healthResponse = await api.get<SystemHealth>('/api/system/health');
        setHealth(healthResponse.data);
      } catch (e) {
        console.warn('Health endpoint not available');
      }

      // Fetch dashboard summary
      try {
        const summaryResponse = await api.get<DashboardSummary>('/api/dashboard/summary');
        setSummary(summaryResponse.data);
      } catch (e) {
        console.warn('Summary endpoint not available');
      }

      // Fetch telemetry for default node
      try {
        setTelemetryLoading(true);
        const telemetryData = await telemetryService.getLatest(selectedNode);
        setLatestTelemetry(selectedNode, telemetryData);

        const history1h = await telemetryService.getHistory(selectedNode, 1);
        setHistory(selectedNode, '1h', history1h);

        const history24h = await telemetryService.getHistory(selectedNode, 24);
        setHistory(selectedNode, '24h', history24h);
      } catch (e) {
        console.warn('Telemetry endpoint not available');
      } finally {
        setTelemetryLoading(false);
      }

      // Fetch AI decisions
      try {
        setAILoading(true);
        const aiData = await aiService.getDecision(selectedNode);
        setDecision(selectedNode, aiData);
      } catch (e) {
        console.warn('AI endpoint not available');
      } finally {
        setAILoading(false);
      }

      // Fetch trades
      try {
        setBlockchainLoading(true);
        const tradesData = await blockchainService.getTrades(50);
        setTrades(tradesData);
      } catch (e) {
        console.warn('Trades endpoint not available');
      } finally {
        setBlockchainLoading(false);
      }
    } catch (error) {
      console.error('Error fetching initial data:', error);
    } finally {
      setIsInitialLoading(false);
    }
  }, [selectedNode, setHealth, setSummary, setLatestTelemetry, setHistory, setTelemetryLoading, setDecision, setAILoading, setTrades, setBlockchainLoading]);

  // Connect to real-time service
  useEffect(() => {
    const connectRealtime = async () => {
      try {
        await realtimeService.connect();
        realtimeService.subscribe(selectedNode);
        realtimeService.subscribeToTrades();
      } catch (error) {
        console.warn('Real-time connection failed:', error);
      }
    };

    connectRealtime();
    fetchInitialData();

    return () => {
      realtimeService.disconnect();
    };
  }, [fetchInitialData, selectedNode]);

  // Refresh data periodically
  useEffect(() => {
    const interval = setInterval(() => {
      // Fetch latest telemetry every 30 seconds as fallback
      telemetryService.getLatest(selectedNode)
        .then((data) => setLatestTelemetry(selectedNode, data))
        .catch(() => {});
    }, 30000);

    return () => clearInterval(interval);
  }, [selectedNode, setLatestTelemetry]);

  const currentTelemetry = latestByNode[selectedNode] || null;
  const currentHistory = historyByNode[selectedNode]?.[selectedPeriod] || [];
  const currentAI = decisionsByNode[selectedNode] || null;

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container flex h-16 items-center justify-between px-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary">
              <Zap className="h-6 w-6 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-xl font-bold">Grid-Guardian</h1>
              <p className="text-xs text-muted-foreground">Energy Trading Dashboard</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <LiveConnectionBadge />
            <Button
              variant="outline"
              size="sm"
              onClick={fetchInitialData}
              disabled={isInitialLoading}
            >
              <RefreshCw className={`mr-2 h-4 w-4 ${isInitialLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container px-4 py-6 space-y-6">
        {/* Summary Strip */}
        <SummaryStrip summary={summary} isLoading={isInitialLoading} />

        {/* Main Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left Column - Telemetry */}
          <div className="lg:col-span-4 space-y-6">
            <TelemetryCard
              nodeId={selectedNode}
              data={currentTelemetry}
              status={currentTelemetry ? 'ACTIVE' : 'OFFLINE'}
              isLoading={telemetryLoading}
            />
            <TelemetryChart
              nodeId={selectedNode}
              data={currentHistory}
              isLoading={telemetryLoading}
            />
          </div>

          {/* Center Column - AI Decision + Forecast */}
          <div className="lg:col-span-4 space-y-6">
            <AIDecisionCard
              nodeId={selectedNode}
              data={currentAI}
              isLoading={aiLoading}
            />
            <ForecastPanel
              data={currentAI}
              isLoading={aiLoading}
            />
          </div>

          {/* Right Column - Blockchain Trades */}
          <div className="lg:col-span-4">
            <TradePanel
              trades={trades}
              activeTrades={activeTrades}
              isLoading={blockchainLoading}
            />
          </div>
        </div>

        {/* Bottom Row - Control Panel & Health */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <ControlPanel onRefresh={fetchInitialData} />
          <HealthStatusCard health={health} isLoading={isInitialLoading} />
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t py-4">
        <div className="container px-4 text-center text-sm text-muted-foreground">
          Grid-Guardian v1.0.0 | AI + Blockchain + Edge Integration
        </div>
      </footer>
    </div>
  );
}
