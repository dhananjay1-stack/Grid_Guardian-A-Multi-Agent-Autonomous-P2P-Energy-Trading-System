import api from './api';
import { AIDecisionData, AIServerStatus, AITradeProposal } from '@/types';

export const aiService = {
  // Get latest AI decision for a node
  async getDecision(nodeId: string): Promise<AIDecisionData> {
    const response = await api.get<AIDecisionData>(`/api/ai/decision/${nodeId}`);
    return response.data;
  },

  // Get AI decision history for a node
  async getHistory(nodeId: string, limit: number = 50): Promise<AIDecisionData[]> {
    const response = await api.get<AIDecisionData[]>(
      `/api/ai/history/${nodeId}?limit=${limit}`
    );
    return response.data;
  },

  // Trigger AI inference manually
  async triggerInference(nodeId: string, context?: Record<string, unknown>): Promise<AIDecisionData> {
    const response = await api.post<AIDecisionData>(`/api/ai/infer/${nodeId}`, { context });
    return response.data;
  },

  // Trigger AI inference with automatic trade processing
  async triggerInferenceWithTrade(
    nodeId: string,
    context?: Record<string, unknown>,
    autoTrade: boolean = true
  ): Promise<{ decision: AIDecisionData; trade_proposal?: AITradeProposal }> {
    const response = await api.post<{ decision: AIDecisionData; trade_proposal?: AITradeProposal }>(
      `/api/ai/infer-with-trade/${nodeId}`,
      { context, auto_trade: autoTrade }
    );
    return response.data;
  },

  // Get all latest AI decisions
  async getAllDecisions(): Promise<AIDecisionData[]> {
    const response = await api.get<AIDecisionData[]>('/api/ai/decisions');
    return response.data;
  },

  // Get AI server status
  async getStatus(): Promise<AIServerStatus> {
    const response = await api.get<AIServerStatus>('/api/ai/status');
    return response.data;
  },

  // Refresh AI server health check
  async refreshHealth(): Promise<{ healthy: boolean; checked_at: string }> {
    const response = await api.post<{ healthy: boolean; checked_at: string }>(
      '/api/ai/refresh-health'
    );
    return response.data;
  },

  // Get pending AI-generated trades
  async getPendingTrades(nodeId?: string): Promise<AITradeProposal[]> {
    const url = nodeId
      ? `/api/ai/trades/pending/${nodeId}`
      : '/api/ai/trades/pending';
    const response = await api.get<AITradeProposal[]>(url);
    return response.data;
  },

  // Get AI trading statistics
  async getTradeStats(): Promise<{
    total_pending: number;
    total_submitted: number;
    total_executed: number;
    total_failed: number;
    total_cancelled: number;
    sell_proposals: number;
    buy_proposals: number;
  }> {
    const response = await api.get<{
      total_pending: number;
      total_submitted: number;
      total_executed: number;
      total_failed: number;
      total_cancelled: number;
      sell_proposals: number;
      buy_proposals: number;
    }>('/api/ai/trades/stats');
    return response.data;
  },

  // Cancel a pending AI trade
  async cancelTrade(tradeId: string, reason?: string): Promise<{ success: boolean; message: string }> {
    const response = await api.post<{ success: boolean; message: string }>(
      `/api/ai/trades/${tradeId}/cancel`,
      { reason }
    );
    return response.data;
  },
};

export default aiService;
