import api from './api';
import { Trade, BlockchainEvent } from '@/types';

export const blockchainService = {
  // Get all trades
  async getTrades(limit: number = 50): Promise<Trade[]> {
    const response = await api.get<Trade[]>(`/api/blockchain/trades?limit=${limit}`);
    return response.data;
  },

  // Get trades for a specific node
  async getNodeTrades(nodeId: string, limit: number = 50): Promise<Trade[]> {
    const response = await api.get<Trade[]>(
      `/api/blockchain/trades/${nodeId}?limit=${limit}`
    );
    return response.data;
  },

  // Get active trades only
  async getActiveTrades(): Promise<Trade[]> {
    const response = await api.get<Trade[]>('/api/blockchain/trades/active');
    return response.data;
  },

  // Get trade by ID
  async getTradeById(tradeId: string): Promise<Trade> {
    const response = await api.get<Trade>(`/api/blockchain/trade/${tradeId}`);
    return response.data;
  },

  // Get blockchain events
  async getEvents(limit: number = 50): Promise<BlockchainEvent[]> {
    const response = await api.get<BlockchainEvent[]>(
      `/api/blockchain/events?limit=${limit}`
    );
    return response.data;
  },

  // Get blockchain status
  async getStatus(): Promise<{ connected: boolean; blockNumber: number }> {
    const response = await api.get<{ connected: boolean; blockNumber: number }>(
      '/api/blockchain/status'
    );
    return response.data;
  },
};

export default blockchainService;
