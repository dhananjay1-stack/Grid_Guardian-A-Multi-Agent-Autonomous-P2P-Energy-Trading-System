'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { SystemHealth } from '@/types';
import { getStatusBgColor } from '@/lib/utils';
import { Activity, Database, Server, Wifi, Clock, Cpu } from 'lucide-react';

interface HealthStatusCardProps {
  health: SystemHealth | null;
  isLoading?: boolean;
}

export function HealthStatusCard({ health, isLoading }: HealthStatusCardProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </CardContent>
      </Card>
    );
  }

  const formatUptime = (seconds: number): string => {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);

    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base font-medium">
            <Activity className="h-4 w-4" />
            System Health
          </CardTitle>
          {health && (
            <Badge className={getStatusBgColor(health.status)}>
              {health.status}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {health ? (
          <>
            {/* Services */}
            <div className="space-y-2">
              <div className="text-sm font-medium text-muted-foreground">Services</div>
              <div className="grid grid-cols-3 gap-2">
                <div className="flex items-center gap-2 rounded-lg bg-muted/50 p-2">
                  <Database className={`h-4 w-4 ${
                    health.services.mongodb.status === 'connected'
                      ? 'text-green-500'
                      : 'text-red-500'
                  }`} />
                  <div>
                    <div className="text-xs font-medium">MongoDB</div>
                    <div className={`text-xs ${
                      health.services.mongodb.status === 'connected'
                        ? 'text-green-500'
                        : 'text-red-500'
                    }`}>
                      {health.services.mongodb.status}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 rounded-lg bg-muted/50 p-2">
                  <Server className={`h-4 w-4 ${
                    health.services.postgresql.status === 'connected'
                      ? 'text-green-500'
                      : 'text-red-500'
                  }`} />
                  <div>
                    <div className="text-xs font-medium">PostgreSQL</div>
                    <div className={`text-xs ${
                      health.services.postgresql.status === 'connected'
                        ? 'text-green-500'
                        : 'text-red-500'
                    }`}>
                      {health.services.postgresql.status}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 rounded-lg bg-muted/50 p-2">
                  <Wifi className={`h-4 w-4 ${
                    health.services.mqtt.status === 'connected'
                      ? 'text-green-500'
                      : 'text-red-500'
                  }`} />
                  <div>
                    <div className="text-xs font-medium">MQTT</div>
                    <div className={`text-xs ${
                      health.services.mqtt.status === 'connected'
                        ? 'text-green-500'
                        : 'text-red-500'
                    }`}>
                      {health.services.mqtt.status}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Memory & Uptime */}
            <div className="grid grid-cols-2 gap-4 pt-2 border-t">
              <div className="flex items-center gap-2">
                <Cpu className="h-4 w-4 text-muted-foreground" />
                <div>
                  <div className="text-xs text-muted-foreground">Memory</div>
                  <div className="text-sm font-medium">{health.memory.heapUsed}</div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Clock className="h-4 w-4 text-muted-foreground" />
                <div>
                  <div className="text-xs text-muted-foreground">Uptime</div>
                  <div className="text-sm font-medium">{formatUptime(health.uptime)}</div>
                </div>
              </div>
            </div>

            {/* Environment */}
            <div className="flex items-center justify-between text-xs text-muted-foreground pt-2 border-t">
              <span>Environment: {health.environment}</span>
              <span>Node {health.nodeVersion}</span>
            </div>
          </>
        ) : (
          <div className="py-4 text-center text-muted-foreground">
            Unable to fetch system health
          </div>
        )}
      </CardContent>
    </Card>
  );
}
