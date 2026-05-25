'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { TelemetryData } from '@/types';
import { formatVoltage, formatCurrent, formatPower, formatRelativeTime, getStatusBgColor } from '@/lib/utils';
import { Zap, Activity, Gauge, Clock } from 'lucide-react';

interface TelemetryCardProps {
  nodeId: string;
  data: TelemetryData | null;
  status?: 'ACTIVE' | 'IDLE' | 'OFFLINE' | 'ERROR';
  isLoading?: boolean;
}

export function TelemetryCard({ nodeId, data, status = 'ACTIVE', isLoading }: TelemetryCardProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-24" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="relative overflow-hidden">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base font-medium">{nodeId}</CardTitle>
          <Badge className={getStatusBgColor(status)}>{status}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {data ? (
          <>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Gauge className="h-4 w-4" />
                <span className="text-sm">Voltage</span>
              </div>
              <span className="font-mono text-lg font-semibold">
                {formatVoltage(data.voltage)}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Activity className="h-4 w-4" />
                <span className="text-sm">Current</span>
              </div>
              <span className="font-mono text-lg font-semibold">
                {formatCurrent(data.current)}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Zap className="h-4 w-4" />
                <span className="text-sm">Power</span>
              </div>
              <span className="font-mono text-lg font-semibold text-primary">
                {formatPower(data.power)}
              </span>
            </div>

            <div className="flex items-center justify-between pt-2 border-t">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Clock className="h-4 w-4" />
                <span className="text-xs">Last Update</span>
              </div>
              <span className="text-xs text-muted-foreground">
                {formatRelativeTime(data.timestamp)}
              </span>
            </div>
          </>
        ) : (
          <div className="py-8 text-center text-muted-foreground">
            No telemetry data available
          </div>
        )}
      </CardContent>
    </Card>
  );
}
