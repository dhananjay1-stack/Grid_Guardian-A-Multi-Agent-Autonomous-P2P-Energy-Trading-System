import { create } from 'zustand';
import { Trade, BlockchainEvent } from '@/types';

interface BlockchainState {
  // All trades
  trades: Trade[];
  // Active trades only
  activeTrades: Trade[];
  // Blockchain events
  events: BlockchainEvent[];
  // Loading states
  isLoading: boolean;
  // Error state
  error: string | null;
  // Actions
  setTrades: (trades: Trade[]) => void;
  addTrade: (trade: Trade) => void;
  updateTrade: (tradeId: string, updates: Partial<Trade>) => void;
  setEvents: (events: BlockchainEvent[]) => void;
  addEvent: (event: BlockchainEvent) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  updateFromLive: (event: BlockchainEvent) => void;
  clearError: () => void;
}

export const useBlockchainStore = create<BlockchainState>((set, get) => ({
  trades: [],
  activeTrades: [],
  events: [],
  isLoading: false,
  error: null,

  setTrades: (trades) =>
    set({
      trades,
      activeTrades: trades.filter((t) =>
        ['PENDING', 'MATCHED', 'EXECUTED'].includes(t.status)
      ),
    }),

  addTrade: (trade) =>
    set((state) => {
      const trades = [trade, ...state.trades].slice(0, 100);
      return {
        trades,
        activeTrades: trades.filter((t) =>
          ['PENDING', 'MATCHED', 'EXECUTED'].includes(t.status)
        ),
      };
    }),

  updateTrade: (tradeId, updates) =>
    set((state) => {
      const trades = state.trades.map((t) =>
        t.trade_id === tradeId ? { ...t, ...updates } : t
      );
      return {
        trades,
        activeTrades: trades.filter((t) =>
          ['PENDING', 'MATCHED', 'EXECUTED'].includes(t.status)
        ),
      };
    }),

  setEvents: (events) => set({ events }),

  addEvent: (event) =>
    set((state) => ({
      events: [event, ...state.events].slice(0, 100),
    })),

  setLoading: (loading) => set({ isLoading: loading }),
  setError: (error) => set({ error }),

  updateFromLive: (event) => {
    const { events, trades } = get();

    // Add event
    set({
      events: [event, ...events].slice(0, 100),
    });

    // If it's a trade event, update trade status
    if (event.event_type.includes('TRADE') && event.payload) {
      const payload = event.payload as { trade_id?: string; status?: string };
      if (payload.trade_id && payload.status) {
        const updatedTrades = trades.map((t) =>
          t.trade_id === payload.trade_id
            ? { ...t, status: payload.status as Trade['status'] }
            : t
        );
        set({
          trades: updatedTrades,
          activeTrades: updatedTrades.filter((t) =>
            ['PENDING', 'MATCHED', 'EXECUTED'].includes(t.status)
          ),
        });
      }
    }
  },

  clearError: () => set({ error: null }),
}));
