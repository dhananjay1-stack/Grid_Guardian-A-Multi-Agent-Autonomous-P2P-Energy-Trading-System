'use client';

import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import simulationService, { SimulationState, ProsumerState, Trade } from '@/services/simulation.service';
import {
  Sun,
  Battery,
  Zap,
  TrendingUp,
  TrendingDown,
  Play,
  Pause,
  RotateCcw,
  SkipForward,
  Clock,
  DollarSign,
  ArrowRightLeft,
} from 'lucide-react';

function formatKwh(value: number): string {
  return `${value.toFixed(2)} kWh`;
}

function formatKw(value: number): string {
  return `${value.toFixed(2)} kW`;
}

function formatPrice(value: number): string {
  return `$${value.toFixed(4)}`;
}

function formatHour(hour: number): string {
  const h = Math.floor(hour);
  const m = Math.round((hour % 1) * 60);
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`;
}

// Battery visualization component
function BatteryIndicator({ soc_percent, is_charging, is_discharging }: {
  soc_percent: number;
  is_charging: boolean;
  is_discharging: boolean;
}) {
  const getColor = () => {
    if (soc_percent < 20) return 'bg-red-500';
    if (soc_percent < 50) return 'bg-yellow-500';
    return 'bg-green-500';
  };

  return (
    <div className="relative w-16 h-8 border-2 border-gray-400 rounded-sm flex items-center">
      <div className="absolute right-[-4px] top-1/2 -translate-y-1/2 w-1 h-3 bg-gray-400 rounded-r" />
      <div
        className={`h-full ${getColor()} transition-all duration-500`}
        style={{ width: `${Math.max(2, soc_percent)}%` }}
      />
      {(is_charging || is_discharging) && (
        <Zap className={`absolute inset-0 m-auto h-4 w-4 ${is_charging ? 'text-yellow-300' : 'text-blue-300'} animate-pulse`} />
      )}
    </div>
  );
}

// Prosumer card component
function ProsumerCard({ prosumer }: { prosumer: ProsumerState }) {
  const isProducing = prosumer.net_power_kw > 0.1;
  const isConsuming = prosumer.net_power_kw < -0.1;

  return (
    <Card className="w-full">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium">{prosumer.name}</CardTitle>
          <Badge variant={isProducing ? 'default' : isConsuming ? 'destructive' : 'secondary'}>
            {isProducing ? 'Surplus' : isConsuming ? 'Deficit' : 'Balanced'}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Power flow */}
        <div className="grid grid-cols-3 gap-2 text-sm">
          <div className="flex items-center gap-1">
            <Sun className="h-4 w-4 text-yellow-500" />
            <span>{formatKw(prosumer.solar_generation_kw)}</span>
          </div>
          <div className="flex items-center gap-1">
            <Zap className="h-4 w-4 text-blue-500" />
            <span>{formatKw(prosumer.load_demand_kw)}</span>
          </div>
          <div className="flex items-center gap-1">
            {prosumer.net_power_kw >= 0 ? (
              <TrendingUp className="h-4 w-4 text-green-500" />
            ) : (
              <TrendingDown className="h-4 w-4 text-red-500" />
            )}
            <span className={prosumer.net_power_kw >= 0 ? 'text-green-500' : 'text-red-500'}>
              {formatKw(Math.abs(prosumer.net_power_kw))}
            </span>
          </div>
        </div>

        {/* Battery */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Battery className="h-4 w-4" />
            <BatteryIndicator
              soc_percent={prosumer.battery.soc_percent}
              is_charging={prosumer.battery.is_charging}
              is_discharging={prosumer.battery.is_discharging}
            />
          </div>
          <span className="text-sm">
            {prosumer.battery.soc_percent.toFixed(0)}% ({formatKwh(prosumer.battery.soc_kwh)})
          </span>
        </div>

        {/* Trading status */}
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">Trade Status:</span>
          <Badge variant="outline" className="text-xs">
            {prosumer.trade_status.toUpperCase()}
          </Badge>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-1 text-xs text-muted-foreground">
          <span>P2P Sold: {formatKwh(prosumer.total_p2p_sold)}</span>
          <span>P2P Bought: {formatKwh(prosumer.total_p2p_bought)}</span>
        </div>
      </CardContent>
    </Card>
  );
}

// Order book component
function OrderBook({ offers, bids }: {
  offers: Array<{ price: number; quantity: number; prosumer: string }>;
  bids: Array<{ price: number; quantity: number; prosumer: string }>;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <ArrowRightLeft className="h-4 w-4" />
          Order Book
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-4">
          {/* Bids (buys) */}
          <div>
            <h4 className="text-xs font-medium mb-2 text-green-500">Bids (Buy)</h4>
            <div className="space-y-1">
              {bids.length > 0 ? bids.slice(0, 5).map((bid, i) => (
                <div key={i} className="text-xs flex justify-between bg-green-500/10 px-2 py-1 rounded">
                  <span>{bid.quantity.toFixed(2)} kWh</span>
                  <span>{formatPrice(bid.price)}</span>
                </div>
              )) : (
                <div className="text-xs text-muted-foreground">No bids</div>
              )}
            </div>
          </div>

          {/* Offers (sells) */}
          <div>
            <h4 className="text-xs font-medium mb-2 text-red-500">Offers (Sell)</h4>
            <div className="space-y-1">
              {offers.length > 0 ? offers.slice(0, 5).map((offer, i) => (
                <div key={i} className="text-xs flex justify-between bg-red-500/10 px-2 py-1 rounded">
                  <span>{offer.quantity.toFixed(2)} kWh</span>
                  <span>{formatPrice(offer.price)}</span>
                </div>
              )) : (
                <div className="text-xs text-muted-foreground">No offers</div>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// Recent trades component
function RecentTrades({ trades }: { trades: Trade[] }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Recent P2P Trades</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2 max-h-48 overflow-y-auto">
          {trades.length > 0 ? trades.slice(0, 10).map((trade) => (
            <div key={trade.trade_id} className="text-xs border rounded p-2">
              <div className="flex justify-between items-center">
                <span className="font-mono">{trade.trade_id}</span>
                <Badge variant="outline" className="text-xs">
                  {trade.status}
                </Badge>
              </div>
              <div className="flex items-center gap-1 mt-1 text-muted-foreground">
                <span>{trade.seller_name}</span>
                <span>→</span>
                <span>{trade.buyer_name}</span>
              </div>
              <div className="flex justify-between mt-1">
                <span>{trade.quantity_kwh.toFixed(3)} kWh</span>
                <span>{formatPrice(trade.total_price)}</span>
              </div>
            </div>
          )) : (
            <div className="text-center text-muted-foreground py-4">
              No trades yet
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// Main simulation dashboard
export function SimulationDashboard() {
  const [state, setState] = useState<SimulationState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const fetchState = useCallback(async () => {
    try {
      const data = await simulationService.getState();
      setState(data);
      setError(null);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch simulation state');
    } finally {
      setLoading(false);
    }
  }, []);

  const handleInitialize = async () => {
    try {
      await simulationService.initialize();
      await fetchState();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleStart = async () => {
    try {
      await simulationService.start(10); // 10x speed
      setAutoRefresh(true);
      await fetchState();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleStop = async () => {
    try {
      await simulationService.stop();
      setAutoRefresh(false);
      await fetchState();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleStep = async () => {
    try {
      await simulationService.step();
      await fetchState();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleReset = async () => {
    try {
      await simulationService.reset();
      setAutoRefresh(false);
      await fetchState();
    } catch (err: any) {
      setError(err.message);
    }
  };

  // Initial fetch
  useEffect(() => {
    fetchState();
  }, [fetchState]);

  // Auto-refresh when running
  useEffect(() => {
    if (autoRefresh) {
      const interval = setInterval(fetchState, 2000);
      return () => clearInterval(interval);
    }
  }, [autoRefresh, fetchState]);

  if (loading && !state) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Control bar */}
      <Card>
        <CardContent className="py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={handleInitialize}
                disabled={(state?.prosumer_count ?? 0) > 0}
              >
                Initialize
              </Button>
              {state?.is_running ? (
                <Button size="sm" variant="outline" onClick={handleStop}>
                  <Pause className="h-4 w-4 mr-1" /> Stop
                </Button>
              ) : (
                <Button size="sm" onClick={handleStart} disabled={(state?.prosumer_count ?? 0) === 0}>
                  <Play className="h-4 w-4 mr-1" /> Start
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={handleStep}
                disabled={state?.is_running || state?.prosumer_count === 0}
              >
                <SkipForward className="h-4 w-4 mr-1" /> Step
              </Button>
              <Button size="sm" variant="outline" onClick={handleReset}>
                <RotateCcw className="h-4 w-4 mr-1" /> Reset
              </Button>
            </div>

            <div className="flex items-center gap-4 text-sm">
              <div className="flex items-center gap-1">
                <Clock className="h-4 w-4" />
                <span>Day {state?.simulation_day || 0}, {formatHour(state?.simulation_hour || 12)}</span>
              </div>
              <div className="flex items-center gap-1">
                <DollarSign className="h-4 w-4" />
                <span>Market: {formatPrice(state?.market?.current_price || 0.15)}/kWh</span>
              </div>
              <Badge variant={state?.is_running ? 'default' : 'secondary'}>
                {state?.is_running ? 'Running' : 'Stopped'}
              </Badge>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="bg-red-100 text-red-700 p-3 rounded">
          {error}
        </div>
      )}

      {/* Main content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Prosumers column */}
        <div className="lg:col-span-2 space-y-4">
          <h3 className="text-lg font-semibold">Virtual Prosumers</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {state?.prosumers?.map((prosumer) => (
              <ProsumerCard key={prosumer.prosumer_id} prosumer={prosumer} />
            ))}
          </div>

          {/* Market stats */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Simulation Statistics</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <div className="text-muted-foreground">Total Solar</div>
                  <div className="font-medium">{formatKwh(state?.stats?.total_solar_kwh || 0)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">Total Load</div>
                  <div className="font-medium">{formatKwh(state?.stats?.total_load_kwh || 0)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">P2P Volume</div>
                  <div className="font-medium">{formatKwh(state?.stats?.total_p2p_volume || 0)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">Tick Count</div>
                  <div className="font-medium">{state?.tick_count || 0}</div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Market column */}
        <div className="space-y-4">
          <OrderBook
            offers={state?.order_book?.offers || []}
            bids={state?.order_book?.bids || []}
          />
          <RecentTrades trades={state?.recent_trades || []} />

          {/* Market summary */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Market Summary</CardTitle>
            </CardHeader>
            <CardContent className="text-sm space-y-2">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Open Offers:</span>
                <span>{state?.market?.open_offers || 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Open Bids:</span>
                <span>{state?.market?.open_bids || 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Total Matches:</span>
                <span>{state?.market?.stats?.total_matches || 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Grid Buy Price:</span>
                <span>{formatPrice(state?.market?.grid_buy_price || 0.20)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Grid Sell Price:</span>
                <span>{formatPrice(state?.market?.grid_sell_price || 0.08)}</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

export default SimulationDashboard;
