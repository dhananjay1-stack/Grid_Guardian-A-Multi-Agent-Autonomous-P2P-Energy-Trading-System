'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { AIDecisionData } from '@/types';
import { formatPower } from '@/lib/utils';
import { Sun, Lightbulb, BarChart3 } from 'lucide-react';

interface ForecastPanelProps {
  data: AIDecisionData | null;
  isLoading?: boolean;
}

export function ForecastPanel({ data, isLoading }: ForecastPanelProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base font-medium">
          <BarChart3 className="h-4 w-4" />
          Forecasts
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {data ? (
          <>
            {/* Forecasted Load */}
            <div className="rounded-lg bg-orange-500/10 p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2 text-orange-500">
                  <Lightbulb className="h-5 w-5" />
                  <span className="text-sm font-medium">Forecasted Load</span>
                </div>
              </div>
              <div className="text-2xl font-bold text-orange-500">
                {formatPower(data.forecasted_load)}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                Expected power consumption
              </div>
            </div>

            {/* Forecasted Solar */}
            <div className="rounded-lg bg-yellow-500/10 p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2 text-yellow-500">
                  <Sun className="h-5 w-5" />
                  <span className="text-sm font-medium">Forecasted Solar</span>
                </div>
              </div>
              <div className="text-2xl font-bold text-yellow-500">
                {formatPower(data.forecasted_solar)}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                Expected solar generation
              </div>
            </div>

            {/* Net Energy */}
            <div className="rounded-lg bg-muted p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-muted-foreground">Net Energy</span>
              </div>
              <div className={`text-xl font-bold ${
                data.forecasted_solar - data.forecasted_load >= 0
                  ? 'text-green-500'
                  : 'text-red-500'
              }`}>
                {data.forecasted_solar - data.forecasted_load >= 0 ? '+' : ''}
                {formatPower(data.forecasted_solar - data.forecasted_load)}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {data.forecasted_solar - data.forecasted_load >= 0
                  ? 'Surplus energy available'
                  : 'Energy deficit expected'}
              </div>
            </div>
          </>
        ) : (
          <div className="py-8 text-center text-muted-foreground">
            No forecast data available
          </div>
        )}
      </CardContent>
    </Card>
  );
}
