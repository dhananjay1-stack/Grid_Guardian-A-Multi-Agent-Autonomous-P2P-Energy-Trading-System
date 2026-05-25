'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { TelemetryData } from '@/types';
import { formatPower, formatTimestamp } from '@/lib/utils';
import { useTelemetryStore } from '@/store';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';

interface TelemetryChartProps {
  nodeId: string;
  data: TelemetryData[];
  isLoading?: boolean;
}

export function TelemetryChart({ nodeId, data, isLoading }: TelemetryChartProps) {
  const { selectedPeriod, setSelectedPeriod } = useTelemetryStore();

  const chartData = data.map((item) => ({
    time: new Date(item.timestamp * 1000).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
    }),
    voltage: item.voltage,
    current: item.current,
    power: item.power,
    timestamp: item.timestamp,
  }));

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className="h-6 w-48" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[300px] w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base font-medium">
            Telemetry History - {nodeId}
          </CardTitle>
          <div className="flex gap-2">
            <Button
              variant={selectedPeriod === '1h' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSelectedPeriod('1h')}
            >
              1 Hour
            </Button>
            <Button
              variant={selectedPeriod === '24h' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSelectedPeriod('24h')}
            >
              24 Hours
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis
                dataKey="time"
                className="text-xs"
                tick={{ fill: 'hsl(var(--muted-foreground))' }}
              />
              <YAxis
                yAxisId="power"
                orientation="left"
                className="text-xs"
                tick={{ fill: 'hsl(var(--muted-foreground))' }}
                label={{ value: 'Power (W)', angle: -90, position: 'insideLeft' }}
              />
              <YAxis
                yAxisId="voltage"
                orientation="right"
                className="text-xs"
                tick={{ fill: 'hsl(var(--muted-foreground))' }}
                label={{ value: 'Voltage (V)', angle: 90, position: 'insideRight' }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--background))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: '8px',
                }}
                labelFormatter={(value, payload) => {
                  if (payload && payload[0]) {
                    return formatTimestamp(payload[0].payload.timestamp);
                  }
                  return value;
                }}
                formatter={(value: number, name: string) => {
                  if (name === 'power') return [formatPower(value), 'Power'];
                  if (name === 'voltage') return [`${value.toFixed(1)} V`, 'Voltage'];
                  if (name === 'current') return [`${value.toFixed(2)} A`, 'Current'];
                  return [value, name];
                }}
              />
              <Legend />
              <Line
                yAxisId="power"
                type="monotone"
                dataKey="power"
                stroke="hsl(var(--primary))"
                strokeWidth={2}
                dot={false}
                name="power"
              />
              <Line
                yAxisId="voltage"
                type="monotone"
                dataKey="voltage"
                stroke="#22c55e"
                strokeWidth={2}
                dot={false}
                name="voltage"
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-[300px] items-center justify-center text-muted-foreground">
            No history data available
          </div>
        )}
      </CardContent>
    </Card>
  );
}
