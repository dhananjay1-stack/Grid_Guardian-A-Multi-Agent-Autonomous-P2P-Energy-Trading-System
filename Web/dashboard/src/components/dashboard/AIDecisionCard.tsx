'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { AIDecisionData } from '@/types';
import { formatConfidence, formatRelativeTime, formatPower, getDecisionBgColor } from '@/lib/utils';
import {
  Brain,
  TrendingUp,
  TrendingDown,
  Minus,
  Clock,
  Target,
  Zap,
  Battery,
  BatteryCharging,
  ArrowRightLeft,
  Activity,
  AlertCircle,
  RefreshCw,
  Cpu,
  Thermometer,
} from 'lucide-react';

interface AIDecisionCardProps {
  nodeId: string;
  data: AIDecisionData | null;
  isLoading?: boolean;
  onRefresh?: () => void;
}

export function AIDecisionCard({ nodeId, data, isLoading, onRefresh }: AIDecisionCardProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </CardContent>
      </Card>
    );
  }

  const getDecisionIcon = (decision: string) => {
    switch (decision) {
      case 'BUY':
        return <TrendingDown className="h-6 w-6" />;
      case 'SELL':
        return <TrendingUp className="h-6 w-6" />;
      case 'HOLD':
        return <Minus className="h-6 w-6" />;
      case 'CHARGE':
        return <BatteryCharging className="h-6 w-6" />;
      case 'DISCHARGE':
        return <Battery className="h-6 w-6" />;
      default:
        return <Brain className="h-6 w-6" />;
    }
  };

  const getActionBadgeColor = (actionName: string | undefined) => {
    if (!actionName) return 'bg-gray-100 text-gray-800';
    if (actionName.includes('charge')) return 'bg-green-100 text-green-800';
    if (actionName.includes('discharge') || actionName.includes('sell')) return 'bg-orange-100 text-orange-800';
    return 'bg-gray-100 text-gray-800';
  };

  const formatActionKw = (kw: number | undefined) => {
    if (kw === undefined || kw === 0) return '0 kW';
    const sign = kw > 0 ? '+' : '';
    return `${sign}${kw.toFixed(1)} kW`;
  };

  const getPolicyBadgeColor = (policy: string | undefined) => {
    if (!policy) return 'bg-gray-100 text-gray-800';
    const p = policy.toUpperCase();
    if (p === 'BC' || p === 'FALLBACK') return 'bg-blue-100 text-blue-800';
    if (p === 'CQL') return 'bg-purple-100 text-purple-800';
    if (p === 'DT') return 'bg-green-100 text-green-800';
    if (p === 'ERROR') return 'bg-red-100 text-red-800';
    return 'bg-gray-100 text-gray-800';
  };

  const getConditionBadgeColor = (condition: string | undefined) => {
    if (!condition) return 'bg-gray-100 text-gray-800';
    const c = condition.toLowerCase();
    if (c === 'stable' || c === 'normal' || c === 'off_peak') return 'bg-green-100 text-green-800';
    if (c === 'uncertain' || c === 'high_pv' || c === 'high_load' || c === 'peak_price') return 'bg-yellow-100 text-yellow-800';
    if (c === 'risky' || c === 'low_soc') return 'bg-orange-100 text-orange-800';
    if (c === 'degraded' || c === 'fault' || c === 'stress_test') return 'bg-red-100 text-red-800';
    return 'bg-gray-100 text-gray-800';
  };

  const formatCondition = (condition: string | undefined) => {
    if (!condition) return '';
    return condition.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  };

  return (
    <Card className="relative overflow-hidden">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base font-medium">
            <Brain className="h-4 w-4" />
            AI Decision Engine
          </CardTitle>
          {onRefresh && (
            <Button variant="ghost" size="sm" onClick={onRefresh} className="h-8 w-8 p-0">
              <RefreshCw className="h-4 w-4" />
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">Node: {nodeId}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        {data ? (
          <>
            {/* Main Decision */}
            <div className="flex items-center justify-center py-3">
              <Badge className={`${getDecisionBgColor(data.decision)} text-xl px-5 py-2 font-bold flex items-center gap-2`}>
                {getDecisionIcon(data.decision)}
                {data.decision}
              </Badge>
            </div>

            {/* Action Details */}
            {data.action_name && (
              <div className="flex items-center justify-between bg-muted/50 rounded-lg p-2">
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm">Action</span>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className={getActionBadgeColor(data.action_name)}>
                    {data.action_name?.replace(/_/g, ' ')}
                  </Badge>
                  <span className="font-mono text-sm font-semibold">
                    {formatActionKw(data.action_kw)}
                  </span>
                </div>
              </div>
            )}

            {/* Confidence */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Target className="h-4 w-4" />
                <span className="text-sm">Confidence</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="h-2 w-24 rounded-full bg-muted overflow-hidden">
                  <div
                    className={`h-full transition-all duration-500 ${
                      data.confidence >= 0.7
                        ? 'bg-green-500'
                        : data.confidence >= 0.5
                        ? 'bg-yellow-500'
                        : 'bg-red-500'
                    }`}
                    style={{ width: `${data.confidence * 100}%` }}
                  />
                </div>
                <span className="font-mono text-sm font-semibold">
                  {formatConfidence(data.confidence)}
                </span>
              </div>
            </div>

            {/* Trade Action (if applicable) */}
            {data.trade_action && (
              <div className="flex items-center justify-between bg-blue-50 dark:bg-blue-950 rounded-lg p-2">
                <div className="flex items-center gap-2 text-blue-700 dark:text-blue-300">
                  <ArrowRightLeft className="h-4 w-4" />
                  <span className="text-sm font-medium">Trade Signal</span>
                </div>
                <Badge className="bg-blue-600 text-white">
                  {data.trade_action}
                </Badge>
              </div>
            )}

            {/* Recommended Quantity */}
            {data.recommended_quantity !== undefined && data.recommended_quantity > 0 && (
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Zap className="h-4 w-4" />
                  <span className="text-sm">Recommended Qty</span>
                </div>
                <span className="font-mono text-sm font-semibold">
                  {data.recommended_quantity.toFixed(3)} kWh
                </span>
              </div>
            )}

            {/* Net Power */}
            {data.net_power_kw !== undefined && (
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Activity className="h-4 w-4" />
                  <span className="text-sm">Net Power</span>
                </div>
                <span className={`font-mono text-sm font-semibold ${
                  data.net_power_kw > 0 ? 'text-green-600' : data.net_power_kw < 0 ? 'text-orange-600' : ''
                }`}>
                  {data.net_power_kw > 0 ? '+' : ''}{data.net_power_kw.toFixed(2)} kW
                </span>
              </div>
            )}

            {/* Model Info */}
            {data.is_mock && (
              <div className="flex items-center gap-2 text-amber-600 bg-amber-50 dark:bg-amber-950 rounded-lg p-2">
                <AlertCircle className="h-4 w-4" />
                <span className="text-xs">Using mock predictions (AI server offline)</span>
              </div>
            )}

            {/* Policy Selection Info */}
            {(data.selected_model || data.condition) && !data.is_mock && (
              <div className="bg-muted/30 rounded-lg p-2 space-y-2">
                {/* Selected Policy/Model */}
                {data.selected_model && (
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Cpu className="h-4 w-4" />
                      <span className="text-xs">Policy</span>
                    </div>
                    <Badge variant="outline" className={`text-xs ${getPolicyBadgeColor(data.selected_model)}`}>
                      {data.selected_model}
                    </Badge>
                  </div>
                )}

                {/* Operating Condition */}
                {data.condition && (
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Thermometer className="h-4 w-4" />
                      <span className="text-xs">Condition</span>
                    </div>
                    <Badge variant="outline" className={`text-xs ${getConditionBadgeColor(data.condition)}`}>
                      {formatCondition(data.condition)}
                    </Badge>
                  </div>
                )}

                {/* Condition Reason */}
                {data.condition_reason && (
                  <div className="text-xs text-muted-foreground text-right">
                    {data.condition_reason.replace(/_/g, ' ')}
                  </div>
                )}
              </div>
            )}

            {/* Model Version */}
            {data.model_version && !data.is_mock && (
              <div className="flex items-center justify-between text-xs text-muted-foreground pt-1">
                <span>Model</span>
                <span className="font-mono">{data.model_version}</span>
              </div>
            )}

            {/* Last Update */}
            <div className="flex items-center justify-between pt-2 border-t">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Clock className="h-4 w-4" />
                <span className="text-xs">Last Decision</span>
              </div>
              <span className="text-xs text-muted-foreground">
                {formatRelativeTime(data.timestamp)}
              </span>
            </div>
          </>
        ) : (
          <div className="py-8 text-center text-muted-foreground">
            <Brain className="h-12 w-12 mx-auto mb-2 opacity-50" />
            <p>No AI decision available</p>
            <p className="text-xs mt-1">Waiting for telemetry data...</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
