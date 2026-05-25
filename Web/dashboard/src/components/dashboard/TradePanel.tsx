'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Trade } from '@/types';
import { formatEnergy, formatPrice, formatTimestamp, getStatusBgColor } from '@/lib/utils';
import { ArrowUpRight, ArrowDownRight, Link2 } from 'lucide-react';

interface TradePanelProps {
  trades: Trade[];
  activeTrades: Trade[];
  isLoading?: boolean;
}

export function TradePanel({ trades, activeTrades, isLoading }: TradePanelProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base font-medium">
            <Link2 className="h-4 w-4" />
            Blockchain Trades
          </CardTitle>
          <Badge variant="outline">
            {activeTrades.length} Active
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        {trades.length > 0 ? (
          <div className="space-y-3 max-h-[400px] overflow-y-auto pr-2">
            {trades.slice(0, 10).map((trade) => (
              <div
                key={trade.trade_id}
                className="rounded-lg border p-3 hover:bg-muted/50 transition-colors"
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    {trade.trade_type === 'SELL' ? (
                      <ArrowUpRight className="h-4 w-4 text-green-500" />
                    ) : (
                      <ArrowDownRight className="h-4 w-4 text-blue-500" />
                    )}
                    <span className="font-mono text-sm font-medium">
                      {trade.trade_id}
                    </span>
                  </div>
                  <Badge className={getStatusBgColor(trade.status)} variant="outline">
                    {trade.status}
                  </Badge>
                </div>

                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <span className="text-muted-foreground">Node:</span>{' '}
                    <span className="font-medium">{trade.node_id}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Type:</span>{' '}
                    <span className={`font-medium ${
                      trade.trade_type === 'SELL' ? 'text-green-500' : 'text-blue-500'
                    }`}>
                      {trade.trade_type}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Qty:</span>{' '}
                    <span className="font-medium">{formatEnergy(trade.energy_amount)}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Total:</span>{' '}
                    <span className="font-medium">{formatPrice(trade.total_price)}</span>
                  </div>
                </div>

                {trade.blockchain_tx_hash && (
                  <div className="mt-2 pt-2 border-t">
                    <span className="text-xs text-muted-foreground">TX: </span>
                    <span className="text-xs font-mono">
                      {trade.blockchain_tx_hash.substring(0, 20)}...
                    </span>
                  </div>
                )}

                <div className="mt-2 text-xs text-muted-foreground">
                  {formatTimestamp(new Date(trade.created_at).getTime() / 1000)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-8 text-center text-muted-foreground">
            No trades recorded
          </div>
        )}
      </CardContent>
    </Card>
  );
}
