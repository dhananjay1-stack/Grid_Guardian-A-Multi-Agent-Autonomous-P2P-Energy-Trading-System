'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Link2,
  FileCheck,
  Clock,
  CheckCircle2,
  XCircle,
  Loader2,
  Hash,
  ArrowRightLeft,
  IndianRupee,
  ExternalLink,
} from 'lucide-react';

interface Settlement {
  trade_id: string;
  tx_hash?: string;
  status: 'pending' | 'locked' | 'delivered' | 'settled' | 'disputed' | 'refunded';
  seller_id: string;
  buyer_id: string;
  quantity_kwh: number;
  total_price: number;
  timestamp: number;
}

interface BlockchainStatus {
  is_connected: boolean;
  network: string;
  block_number?: number;
  pending_trades: number;
  total_settlements: number;
  recent_settlements: Settlement[];
}

interface BlockchainPanelProps {
  status: BlockchainStatus | null;
  isLoading?: boolean;
}

const getSettlementStatusColor = (status: Settlement['status']) => {
  switch (status) {
    case 'settled':
      return 'bg-green-100 text-green-800';
    case 'delivered':
      return 'bg-blue-100 text-blue-800';
    case 'locked':
      return 'bg-yellow-100 text-yellow-800';
    case 'pending':
      return 'bg-gray-100 text-gray-800';
    case 'disputed':
      return 'bg-red-100 text-red-800';
    case 'refunded':
      return 'bg-orange-100 text-orange-800';
    default:
      return 'bg-gray-100 text-gray-800';
  }
};

const getSettlementIcon = (status: Settlement['status']) => {
  switch (status) {
    case 'settled':
      return <CheckCircle2 className="h-3 w-3" />;
    case 'delivered':
      return <FileCheck className="h-3 w-3" />;
    case 'locked':
      return <Link2 className="h-3 w-3" />;
    case 'pending':
      return <Loader2 className="h-3 w-3 animate-spin" />;
    case 'disputed':
    case 'refunded':
      return <XCircle className="h-3 w-3" />;
    default:
      return <Clock className="h-3 w-3" />;
  }
};

const truncateHash = (hash: string) => {
  if (!hash) return '';
  return `${hash.slice(0, 6)}...${hash.slice(-4)}`;
};

const formatPrice = (price: number) => {
  return `₹${price.toFixed(2)}`;
};

export function BlockchainPanel({ status, isLoading }: BlockchainPanelProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base font-medium">
            <Link2 className="h-4 w-4" />
            Blockchain Settlement
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="animate-pulse space-y-3">
            <div className="h-4 bg-muted rounded w-3/4" />
            <div className="h-4 bg-muted rounded w-1/2" />
            <div className="h-20 bg-muted rounded" />
          </div>
        </CardContent>
      </Card>
    );
  }

  const mockStatus: BlockchainStatus = status || {
    is_connected: true,
    network: 'Hardhat Local',
    block_number: 12345,
    pending_trades: 0,
    total_settlements: 3,
    recent_settlements: [
      {
        trade_id: 'trade-003',
        tx_hash: '0x1234567890abcdef1234567890abcdef12345678',
        status: 'settled',
        seller_id: 'prosumer-a',
        buyer_id: 'prosumer-b',
        quantity_kwh: 0.42,
        total_price: 2.1,
        timestamp: Date.now() - 60000,
      },
      {
        trade_id: 'trade-002',
        tx_hash: '0xabcdef1234567890abcdef1234567890abcdef12',
        status: 'settled',
        seller_id: 'prosumer-a',
        buyer_id: 'prosumer-b',
        quantity_kwh: 0.35,
        total_price: 1.75,
        timestamp: Date.now() - 180000,
      },
    ],
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base font-medium">
            <Link2 className="h-4 w-4" />
            Blockchain Settlement
          </CardTitle>
          {mockStatus.is_connected ? (
            <Badge variant="outline" className="bg-green-50 text-green-700 border-green-200 text-xs">
              Connected
            </Badge>
          ) : (
            <Badge variant="outline" className="bg-red-50 text-red-700 border-red-200 text-xs">
              Offline
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Network Info */}
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Network:</span>
            <span className="font-medium">{mockStatus.network}</span>
          </div>
          {mockStatus.block_number && (
            <div className="flex justify-between">
              <span className="text-muted-foreground">Block:</span>
              <span className="font-mono text-xs">{mockStatus.block_number}</span>
            </div>
          )}
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-2 bg-muted/50 rounded-lg p-2">
          <div className="text-center">
            <div className="text-lg font-bold">{mockStatus.pending_trades}</div>
            <div className="text-xs text-muted-foreground">Pending</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-bold">{mockStatus.total_settlements}</div>
            <div className="text-xs text-muted-foreground">Settled</div>
          </div>
        </div>

        {/* Recent Settlements */}
        <div>
          <h4 className="text-xs font-medium text-muted-foreground mb-2">Recent Settlements</h4>
          <ScrollArea style={{ height: '200px' }}>
            <div className="space-y-2">
              {mockStatus.recent_settlements.length > 0 ? (
                mockStatus.recent_settlements.map((settlement) => (
                  <div key={settlement.trade_id} className="border rounded-lg p-2 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs">{settlement.trade_id}</span>
                      <Badge className={`text-xs ${getSettlementStatusColor(settlement.status)}`}>
                        {getSettlementIcon(settlement.status)}
                        <span className="ml-1">{settlement.status}</span>
                      </Badge>
                    </div>

                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                      <ArrowRightLeft className="h-3 w-3" />
                      <span>{settlement.seller_id}</span>
                      <span>→</span>
                      <span>{settlement.buyer_id}</span>
                    </div>

                    <div className="flex justify-between text-xs">
                      <span>{settlement.quantity_kwh.toFixed(3)} kWh</span>
                      <span className="flex items-center gap-1">
                        <IndianRupee className="h-3 w-3" />
                        {formatPrice(settlement.total_price)}
                      </span>
                    </div>

                    {settlement.tx_hash && (
                      <div className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Hash className="h-3 w-3" />
                        <span className="font-mono">{truncateHash(settlement.tx_hash)}</span>
                        <ExternalLink className="h-3 w-3 cursor-pointer hover:text-foreground" />
                      </div>
                    )}
                  </div>
                ))
              ) : (
                <div className="text-center py-4 text-muted-foreground">
                  <FileCheck className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  <p className="text-xs">No settlements yet</p>
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      </CardContent>
    </Card>
  );
}

export default BlockchainPanel;
