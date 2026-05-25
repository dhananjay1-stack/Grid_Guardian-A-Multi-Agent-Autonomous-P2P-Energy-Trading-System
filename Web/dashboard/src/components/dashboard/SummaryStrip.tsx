'use client';

import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { DashboardSummary } from '@/types';
import { formatPower, getStatusBgColor, getDecisionBgColor } from '@/lib/utils';
import { Activity, Zap, BarChart3, Link2, Brain } from 'lucide-react';

interface SummaryStripProps {
  summary: DashboardSummary | null;
  isLoading?: boolean;
}

export function SummaryStrip({ summary, isLoading }: SummaryStripProps) {
  if (isLoading) {
    return (
      <Card className="p-4">
        <div className="flex items-center justify-between gap-4">
          <Skeleton className="h-12 w-32" />
          <Skeleton className="h-12 w-32" />
          <Skeleton className="h-12 w-32" />
          <Skeleton className="h-12 w-32" />
          <Skeleton className="h-12 w-32" />
        </div>
      </Card>
    );
  }

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        {/* System Status */}
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
            <Activity className="h-5 w-5 text-primary" />
          </div>
          <div>
            <div className="text-xs text-muted-foreground">System Status</div>
            <Badge className={getStatusBgColor(summary?.system_status || 'ERROR')}>
              {summary?.system_status || 'UNKNOWN'}
            </Badge>
          </div>
        </div>

        {/* Active Nodes */}
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
            <BarChart3 className="h-5 w-5 text-blue-500" />
          </div>
          <div>
            <div className="text-xs text-muted-foreground">Active Nodes</div>
            <div className="text-lg font-bold">
              {summary?.active_nodes ?? 0}
              <span className="text-sm font-normal text-muted-foreground">
                /{summary?.total_nodes ?? 0}
              </span>
            </div>
          </div>
        </div>

        {/* Total Power */}
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
            <Zap className="h-5 w-5 text-yellow-500" />
          </div>
          <div>
            <div className="text-xs text-muted-foreground">Total Power</div>
            <div className="text-lg font-bold">
              {formatPower(summary?.total_power ?? 0)}
            </div>
          </div>
        </div>

        {/* Avg Voltage */}
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
            <Activity className="h-5 w-5 text-green-500" />
          </div>
          <div>
            <div className="text-xs text-muted-foreground">Avg Voltage</div>
            <div className="text-lg font-bold">
              {(summary?.avg_voltage ?? 0).toFixed(1)} V
            </div>
          </div>
        </div>

        {/* Pending Trades */}
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
            <Link2 className="h-5 w-5 text-purple-500" />
          </div>
          <div>
            <div className="text-xs text-muted-foreground">Pending Trades</div>
            <div className="text-lg font-bold">{summary?.pending_trades ?? 0}</div>
          </div>
        </div>

        {/* Latest AI Decision */}
        {summary?.latest_ai_decision && (
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
              <Brain className="h-5 w-5 text-orange-500" />
            </div>
            <div>
              <div className="text-xs text-muted-foreground">AI Decision</div>
              <Badge className={getDecisionBgColor(summary.latest_ai_decision)}>
                {summary.latest_ai_decision}
              </Badge>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
