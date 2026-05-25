'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EdgeStatusPanel } from '@/components/dashboard/EdgeStatusPanel';
import { BlockchainPanel } from '@/components/dashboard/BlockchainPanel';
import { EventTimeline } from '@/components/dashboard/EventTimeline';
import {
  Play,
  Pause,
  RotateCcw,
  SkipForward,
  Monitor,
  Sun,
  Battery,
  Zap,
  Brain,
  Link2,
  Clock,
  TrendingUp,
  ArrowRightLeft,
  ShieldCheck,
  Activity,
  Wifi,
  WifiOff,
} from 'lucide-react';

// Types
interface ProsumerState {
  prosumer_id: string;
  name: string;
  solar_kw: number;
  load_kw: number;
  net_power_kw: number;
  surplus_kw: number;
  deficit_kw: number;
  battery: {
    soc_kwh: number;
    soc_fraction: number;
    capacity_kwh: number;
  };
  trade_status: string;
}

interface AIDecision {
  action_name: string;
  action_kw: number;
  decision: string;
  trade_action?: string;
  confidence: number;
  selected_policy: string;
  policy_reason: string;
  condition: string;
  condition_confidence: number;
  volatility: number;
  sub_conditions: string[];
  supplementary_override?: boolean;
  source?: string;
}

interface Trade {
  trade_id: string;
  seller_id: string;
  buyer_id: string;
  quantity_kwh: number;
  price_per_kwh: number;
  total_price: number;
  status: string;
  blockchain_tx_hash?: string;
}

interface MarketState {
  current_price: number;
  open_offers: number;
  open_bids: number;
  total_offer_volume: number;
  total_bid_volume: number;
  pending_trades: number;
  stats?: {
    total_offers: number;
    total_bids: number;
    total_matches: number;
    total_volume_kwh: number;
    total_value: number;
  };
}

interface DemoState {
  tick: number;
  hour: number;
  day: number;
  is_running: boolean;
  prosumers: Record<string, ProsumerState>;
  ai_decisions: Record<string, AIDecision>;
  trades: Trade[];
  market: MarketState;
  events: any[];
  edge_status: any;
  blockchain_status: any;
  ai_service_status?: boolean;
}

// Helper functions
function formatHour(hour: number): string {
  const h = Math.floor(hour);
  const m = Math.round((hour % 1) * 60);
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`;
}

function formatKw(value: number): string {
  return `${value.toFixed(2)} kW`;
}

function getTimeOfDayLabel(hour: number): string {
  if (hour < 6) return '🌙 Night';
  if (hour < 10) return '🌅 Morning';
  if (hour < 14) return '☀️ Midday';
  if (hour < 18) return '🌇 Afternoon';
  if (hour < 21) return '🌆 Evening';
  return '🌙 Night';
}

function getDecisionColor(decision: string): string {
  switch (decision) {
    case 'SELL': return 'bg-green-100 text-green-800 border-green-200';
    case 'BUY': return 'bg-blue-100 text-blue-800 border-blue-200';
    case 'CHARGE': return 'bg-yellow-100 text-yellow-800 border-yellow-200';
    case 'DISCHARGE': return 'bg-orange-100 text-orange-800 border-orange-200';
    case 'HOLD': return 'bg-gray-100 text-gray-800 border-gray-200';
    default: return 'bg-gray-100 text-gray-800 border-gray-200';
  }
}

function getTradeActionBadge(tradeAction: string | null | undefined): { text: string; color: string } | null {
  if (!tradeAction) return null;
  switch (tradeAction) {
    case 'SELL': return { text: '📤 SELL', color: 'bg-green-500 text-white' };
    case 'BUY': return { text: '📥 BUY', color: 'bg-blue-500 text-white' };
    default: return null;
  }
}

// Prosumer Card Component (Demo-specific)
function DemoProsumerCard({ prosumer, aiDecision, isLive }: { prosumer: ProsumerState; aiDecision?: AIDecision; isLive: boolean }) {
  const isProducing = prosumer.net_power_kw > 0.1;
  const isConsuming = prosumer.net_power_kw < -0.1;
  const tradeBadge = aiDecision ? getTradeActionBadge(aiDecision.trade_action) : null;

  return (
    <Card className={`w-full transition-all duration-300 ${isLive ? 'border-green-500/30' : 'border-gray-200'}`}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium">{prosumer.name}</CardTitle>
          <div className="flex items-center gap-1">
            {isLive && (
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
              </span>
            )}
            <Badge variant={isProducing ? 'default' : isConsuming ? 'destructive' : 'secondary'}>
              {isProducing ? '⚡ Seller' : isConsuming ? '🔌 Buyer' : '⚖️ Balanced'}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Power flow */}
        <div className="grid grid-cols-3 gap-2 text-sm">
          <div className="flex items-center gap-1">
            <Sun className="h-4 w-4 text-yellow-500" />
            <span>{formatKw(prosumer.solar_kw)}</span>
          </div>
          <div className="flex items-center gap-1">
            <Zap className="h-4 w-4 text-blue-500" />
            <span>{formatKw(prosumer.load_kw)}</span>
          </div>
          <div className="flex items-center gap-1">
            <span className={prosumer.net_power_kw >= 0 ? 'text-green-500 font-medium' : 'text-red-500 font-medium'}>
              {prosumer.net_power_kw >= 0 ? '+' : ''}{formatKw(prosumer.net_power_kw)}
            </span>
          </div>
        </div>

        {/* Battery */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Battery className="h-4 w-4" />
            <div className="w-24 h-3 bg-gray-200 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-700 ease-out ${
                  prosumer.battery.soc_fraction < 0.2
                    ? 'bg-red-500'
                    : prosumer.battery.soc_fraction < 0.5
                    ? 'bg-yellow-500'
                    : 'bg-green-500'
                }`}
                style={{ width: `${prosumer.battery.soc_fraction * 100}%` }}
              />
            </div>
          </div>
          <span className="text-sm font-bold">
            {(prosumer.battery.soc_fraction * 100).toFixed(0)}%
          </span>
        </div>

        {/* AI Decision Summary */}
        {aiDecision && (
          <div className="bg-purple-50 rounded p-2 space-y-1">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-1">
                <Brain className="h-3 w-3 text-purple-600" />
                <span className="text-purple-700 font-medium">AI Model:</span>
              </div>
              <Badge variant="outline" className="text-xs bg-purple-100 text-purple-800">
                {aiDecision.selected_policy}
              </Badge>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">Action:</span>
              <div className="flex items-center gap-1">
                <Badge variant="outline" className={`text-xs ${getDecisionColor(aiDecision.decision)}`}>
                  {aiDecision.action_name?.replace(/_/g, ' ')}
                </Badge>
                {tradeBadge && (
                  <Badge className={`text-[10px] px-1.5 py-0 ${tradeBadge.color}`}>
                    {tradeBadge.text}
                  </Badge>
                )}
              </div>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">Condition:</span>
              <span className="font-medium">{aiDecision.condition?.replace(/_/g, ' ')}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">Confidence:</span>
              <div className="flex items-center gap-1">
                <div className="w-12 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-purple-500 transition-all duration-500"
                    style={{ width: `${(aiDecision.confidence || 0) * 100}%` }}
                  />
                </div>
                <span className="font-medium">{((aiDecision.confidence || 0) * 100).toFixed(0)}%</span>
              </div>
            </div>
          </div>
        )}

        {/* Trade status */}
        <div className="flex items-center justify-between text-xs pt-1 border-t">
          <span className="text-muted-foreground">Trade Status:</span>
          <Badge variant="outline" className={`text-xs ${
            prosumer.trade_status === 'offering' ? 'bg-green-50 text-green-700 border-green-200' :
            prosumer.trade_status === 'bidding' ? 'bg-blue-50 text-blue-700 border-blue-200' :
            prosumer.trade_status === 'matched' ? 'bg-orange-50 text-orange-700 border-orange-200' :
            ''
          }`}>
            {prosumer.trade_status?.toUpperCase() || 'IDLE'}
          </Badge>
        </div>
      </CardContent>
    </Card>
  );
}

// Main Demo Dashboard
export default function DemoPage() {
  const [mounted, setMounted] = useState(false);
  const [demoState, setDemoState] = useState<DemoState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [lastUpdateTime, setLastUpdateTime] = useState<number>(0);
  const autoStartAttempted = useRef(false);
  const fetchCountRef = useRef(0);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Demo control functions
  const fetchState = useCallback(async () => {
    try {
      const res = await fetch('/api/demo/state');
      if (res.ok) {
        const data = await res.json();
        setDemoState(data);
        setIsRunning(data.is_running);
        setIsConnected(true);
        setLastUpdateTime(Date.now());
        setError(null);
        fetchCountRef.current++;
      } else {
        setIsConnected(false);
      }
    } catch (err: any) {
      console.error('Failed to fetch demo state:', err);
      setIsConnected(false);
      if (fetchCountRef.current === 0) {
        setError('Cannot connect to backend at localhost:3000. Please start the backend server first.');
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleStart = async () => {
    try {
      setError(null);
      const res = await fetch('/api/demo/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed: 60 }), // Fast speed for demo
      });
      if (res.ok) {
        setIsRunning(true);
        await fetchState();
      }
    } catch (err: any) {
      setError(`Failed to start demo: ${err.message}`);
    }
  };

  const handleStop = async () => {
    try {
      const res = await fetch('/api/demo/stop', { method: 'POST' });
      if (res.ok) {
        setIsRunning(false);
        await fetchState();
      }
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleStep = async () => {
    try {
      const res = await fetch('/api/demo/step', { method: 'POST' });
      if (res.ok) {
        await fetchState();
      }
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleReset = async () => {
    try {
      const res = await fetch('/api/demo/reset', { method: 'POST' });
      if (res.ok) {
        setIsRunning(false);
        await fetchState();
      }
    } catch (err: any) {
      setError(err.message);
    }
  };

  // Initial fetch + auto-start
  useEffect(() => {
    const init = async () => {
      await fetchState();

      // Auto-start the demo if not already running
      if (!autoStartAttempted.current) {
        autoStartAttempted.current = true;
        try {
          const stateRes = await fetch('/api/demo/state');
          if (stateRes.ok) {
            const stateData = await stateRes.json();
            if (!stateData.is_running) {
              // Auto-start with fast speed
              const startRes = await fetch('/api/demo/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ speed: 60 }),
              });
              if (startRes.ok) {
                setIsRunning(true);
                console.log('Demo auto-started');
              }
            } else {
              setIsRunning(true);
            }
          }
        } catch (e) {
          console.warn('Auto-start failed:', e);
        }
      }
    };
    init();
  }, [fetchState]);

  // Auto-refresh polling — faster when running
  useEffect(() => {
    const interval = setInterval(fetchState, isRunning ? 1500 : 5000);
    return () => clearInterval(interval);
  }, [isRunning, fetchState]);

  // Compute dynamic values from state
  const state = demoState;
  const prosumers = state?.prosumers ? Object.values(state.prosumers) : [];
  const aiDecisions = state?.ai_decisions || {};
  const trades = state?.trades || [];
  const events = state?.events || [];
  const market = state?.market;
  const edgeStatus = state?.edge_status || null;
  const blockchainStatus = state?.blockchain_status || null;

  // Derived market stats — use cumulative stats from market engine
  const settledTrades = trades.filter((t: Trade) => t.status === 'settled');
  const matchedTrades = trades.filter((t: Trade) => t.status === 'matched');
  const totalTradeVolume = (market as any)?.total_volume_kwh || (market?.stats?.total_volume_kwh) || trades.reduce((sum: number, t: Trade) => sum + (t.quantity_kwh || 0), 0);
  const activeOffers = market?.open_offers ?? 0;
  const activeBids = market?.open_bids ?? 0;
  const tradesToday = (market as any)?.total_matches || (market?.stats?.total_matches) || trades.length;
  const totalSettled = (blockchainStatus as any)?.total_settlements || settledTrades.length;

  if (!mounted) return null;

  return (
    <div className="min-h-screen bg-background p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">⚡ Grid Guardian Demo</h1>
          <p className="text-sm text-muted-foreground">
            End-to-end P2P Energy Trading with AI, Blockchain & Edge Runtime
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Connection indicator */}
          <div className="flex items-center gap-1.5 text-xs">
            {isConnected ? (
              <>
                <Wifi className="h-3.5 w-3.5 text-green-500" />
                <span className="text-green-600">Backend Connected</span>
              </>
            ) : (
              <>
                <WifiOff className="h-3.5 w-3.5 text-red-500" />
                <span className="text-red-600">Disconnected</span>
              </>
            )}
          </div>
          {/* AI Service indicator */}
          {state?.ai_service_status !== undefined && (
            <Badge variant="outline" className={`text-xs ${state.ai_service_status ? 'bg-purple-50 text-purple-700 border-purple-200' : 'bg-yellow-50 text-yellow-700 border-yellow-200'}`}>
              <Brain className="h-3 w-3 mr-1" />
              {state.ai_service_status ? 'AI Models Active' : 'Fallback Mode'}
            </Badge>
          )}
          <Badge variant={isRunning ? 'default' : 'secondary'} className={`px-3 py-1 ${isRunning ? 'bg-green-600 hover:bg-green-700' : ''}`}>
            {isRunning ? (
              <><Activity className="h-3 w-3 mr-1 animate-pulse" /> Running</>
            ) : 'Stopped'}
          </Badge>
        </div>
      </div>

      {/* Control Bar */}
      <Card>
        <CardContent className="py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {isRunning ? (
                <Button size="sm" variant="outline" onClick={handleStop}>
                  <Pause className="h-4 w-4 mr-1" /> Stop
                </Button>
              ) : (
                <Button size="sm" onClick={handleStart} className="bg-green-600 hover:bg-green-700">
                  <Play className="h-4 w-4 mr-1" /> Start Demo
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={handleStep} disabled={isRunning}>
                <SkipForward className="h-4 w-4 mr-1" /> Step
              </Button>
              <Button size="sm" variant="outline" onClick={handleReset}>
                <RotateCcw className="h-4 w-4 mr-1" /> Reset
              </Button>
            </div>

            <div className="flex items-center gap-4 text-sm">
              <div className="flex items-center gap-1">
                <Clock className="h-4 w-4" />
                Day {state?.day ?? 0}, {formatHour(state?.hour ?? 12)}
              </div>
              <div className="text-xs text-muted-foreground">
                {getTimeOfDayLabel(state?.hour ?? 12)}
              </div>
              <div className="flex items-center gap-1">
                <Monitor className="h-4 w-4" />
                Tick #{state?.tick ?? 0}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="bg-red-100 text-red-700 p-3 rounded flex items-center gap-2">
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}

      {/* Main Grid - 7 Panels */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Column 1: Prosumers */}
        <div className="space-y-4">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Sun className="h-5 w-5 text-yellow-500" />
            Virtual Prosumers
            {isRunning && <span className="text-xs text-green-600 font-normal">(Live)</span>}
          </h2>
          {prosumers.length > 0 ? (
            prosumers.map((prosumer) => (
              <DemoProsumerCard
                key={prosumer.prosumer_id}
                prosumer={prosumer}
                aiDecision={aiDecisions[prosumer.prosumer_id]}
                isLive={isRunning}
              />
            ))
          ) : (
            <Card className="p-8 text-center text-muted-foreground">
              <Sun className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-sm">Waiting for simulation data...</p>
              <p className="text-xs mt-1">Click "Start Demo" or wait for auto-start</p>
            </Card>
          )}
        </div>

        {/* Column 2: AI & Edge */}
        <div className="space-y-4">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Brain className="h-5 w-5 text-purple-500" />
            AI & Edge
            {isRunning && <span className="text-xs text-green-600 font-normal">(Live)</span>}
          </h2>

          {/* AI Decision Panel */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <Brain className="h-4 w-4 text-purple-600" />
                AI Decision Engine
                {isRunning && (
                  <span className="relative flex h-2 w-2 ml-auto">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-purple-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-purple-500"></span>
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {Object.entries(aiDecisions).length > 0 ? (
                Object.entries(aiDecisions).map(([pid, decision]) => {
                  const tradeBadge = getTradeActionBadge(decision.trade_action);
                  return (
                  <div key={pid} className="border rounded-lg p-2 space-y-1 transition-all duration-300">
                    <div className="flex items-center justify-between">
                      <Badge variant="outline" className="text-xs">{pid.replace('prosumer-', 'P-')}</Badge>
                      <div className="flex items-center gap-1">
                        <Badge className={`text-xs ${
                          decision.selected_policy === 'DT' ? 'bg-blue-100 text-blue-800' :
                          decision.selected_policy === 'CQL' ? 'bg-green-100 text-green-800' :
                          decision.selected_policy === 'BC' ? 'bg-orange-100 text-orange-800' :
                          decision.selected_policy === 'FALLBACK' ? 'bg-yellow-100 text-yellow-800' :
                          'bg-purple-100 text-purple-800'
                        }`}>
                          {decision.selected_policy}
                        </Badge>
                        {tradeBadge && (
                          <Badge className={`text-[10px] px-1.5 py-0 ${tradeBadge.color}`}>
                            {tradeBadge.text}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-muted-foreground">Condition:</span>
                        <span className="ml-1 font-medium">{decision.condition?.replace(/_/g, ' ')}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">Confidence:</span>
                        <span className="ml-1 font-medium">{((decision.confidence || 0) * 100).toFixed(0)}%</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">Action:</span>
                        <Badge variant="outline" className={`ml-1 text-[10px] px-1 py-0 ${getDecisionColor(decision.decision)}`}>
                          {decision.action_name?.replace(/_/g, ' ')}
                        </Badge>
                      </div>
                      <div>
                        <span className="text-muted-foreground">Volatility:</span>
                        <span className="ml-1 font-medium">{((decision.volatility || 0) * 100).toFixed(0)}%</span>
                      </div>
                    </div>
                    {decision.policy_reason && (
                      <p className="text-xs text-muted-foreground italic truncate" title={decision.policy_reason}>{decision.policy_reason}</p>
                    )}
                  </div>
                  );
                })
              ) : (
                <div className="text-center py-4 text-muted-foreground">
                  <Brain className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  <p className="text-xs">Waiting for AI decisions...</p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Edge Status */}
          <EdgeStatusPanel status={edgeStatus} />
        </div>

        {/* Column 3: Market & Blockchain */}
        <div className="space-y-4">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Link2 className="h-5 w-5 text-orange-500" />
            Market & Blockchain
            {isRunning && <span className="text-xs text-green-600 font-normal">(Live)</span>}
          </h2>

          {/* Market Panel - FULLY DYNAMIC */}
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-medium flex items-center gap-2">
                  <TrendingUp className="h-4 w-4 text-orange-500" />
                  P2P Market
                </CardTitle>
                {isRunning && (
                  <Badge variant="outline" className="text-xs bg-green-50 text-green-700 border-green-200">
                    Active
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* Current Price */}
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Current Price:</span>
                <span className="font-bold text-lg">₹{(market?.current_price ?? 0).toFixed(2)}/kWh</span>
              </div>

              {/* Market stats */}
              <div className="grid grid-cols-2 gap-2 bg-muted/50 rounded-lg p-2">
                <div className="text-center">
                  <div className="text-lg font-bold text-green-600">{activeOffers}</div>
                  <div className="text-xs text-muted-foreground">Active Offers</div>
                </div>
                <div className="text-center">
                  <div className="text-lg font-bold text-blue-600">{activeBids}</div>
                  <div className="text-xs text-muted-foreground">Active Bids</div>
                </div>
              </div>

              <div className="space-y-1">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Trades Executed:</span>
                  <span className="font-medium">{tradesToday}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Matched Pending:</span>
                  <span className="font-medium text-orange-600">{matchedTrades.length}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Settled:</span>
                  <span className="font-medium text-green-600">{totalSettled}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Total Volume:</span>
                  <span className="font-medium">{totalTradeVolume.toFixed(3)} kWh</span>
                </div>
              </div>

              {/* Recent trades list */}
              {trades.length > 0 && (
                <div className="border-t pt-2">
                  <h4 className="text-xs font-medium text-muted-foreground mb-1.5">Recent Trades</h4>
                  <div className="space-y-1 max-h-[120px] overflow-y-auto">
                    {trades.slice(-5).reverse().map((trade: Trade, idx: number) => (
                      <div key={trade.trade_id || idx} className="flex items-center justify-between text-xs bg-muted/30 rounded p-1.5">
                        <div className="flex items-center gap-1">
                          <ArrowRightLeft className="h-3 w-3 text-muted-foreground" />
                          <span>{trade.seller_id?.split('-')[1] || 'a'}</span>
                          <span>→</span>
                          <span>{trade.buyer_id?.split('-')[1] || 'b'}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span>{(trade.quantity_kwh || 0).toFixed(2)} kWh</span>
                          <Badge variant="outline" className={`text-[10px] ${
                            trade.status === 'settled' ? 'bg-green-50 text-green-700' :
                            trade.status === 'matched' ? 'bg-orange-50 text-orange-700' :
                            'bg-gray-50 text-gray-700'
                          }`}>
                            {trade.status}
                          </Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Blockchain Panel */}
          <BlockchainPanel status={blockchainStatus} />
        </div>
      </div>

      {/* Event Timeline - Full Width */}
      <EventTimeline events={events} maxHeight="300px" />
    </div>
  );
}
