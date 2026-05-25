import { create } from 'zustand';
import { TelemetryData } from '@/types';

interface TelemetryState {
  // Current telemetry data by node
  latestByNode: Record<string, TelemetryData>;
  // History data by node and period
  historyByNode: Record<string, { '1h': TelemetryData[]; '24h': TelemetryData[] }>;
  // Loading states
  isLoading: boolean;
  isHistoryLoading: boolean;
  // Error state
  error: string | null;
  // Selected period for charts
  selectedPeriod: '1h' | '24h';
  // Actions
  setLatestTelemetry: (nodeId: string, data: TelemetryData) => void;
  setHistory: (nodeId: string, period: '1h' | '24h', data: TelemetryData[]) => void;
  setLoading: (loading: boolean) => void;
  setHistoryLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setSelectedPeriod: (period: '1h' | '24h') => void;
  updateFromLive: (data: TelemetryData) => void;
  clearError: () => void;
}

export const useTelemetryStore = create<TelemetryState>((set, get) => ({
  latestByNode: {},
  historyByNode: {},
  isLoading: false,
  isHistoryLoading: false,
  error: null,
  selectedPeriod: '1h',

  setLatestTelemetry: (nodeId, data) =>
    set((state) => ({
      latestByNode: { ...state.latestByNode, [nodeId]: data },
    })),

  setHistory: (nodeId, period, data) =>
    set((state) => ({
      historyByNode: {
        ...state.historyByNode,
        [nodeId]: {
          ...state.historyByNode[nodeId],
          [period]: data,
        },
      },
    })),

  setLoading: (loading) => set({ isLoading: loading }),
  setHistoryLoading: (loading) => set({ isHistoryLoading: loading }),
  setError: (error) => set({ error }),
  setSelectedPeriod: (period) => set({ selectedPeriod: period }),

  updateFromLive: (data) => {
    const { latestByNode, historyByNode } = get();
    const nodeId = data.node_id;

    // Update latest
    set({
      latestByNode: { ...latestByNode, [nodeId]: data },
    });

    // Add to 1h history (keep last 360 entries for 10-second intervals)
    const currentHistory = historyByNode[nodeId]?.['1h'] || [];
    const newHistory = [...currentHistory, data].slice(-360);
    set({
      historyByNode: {
        ...historyByNode,
        [nodeId]: {
          ...historyByNode[nodeId],
          '1h': newHistory,
        },
      },
    });
  },

  clearError: () => set({ error: null }),
}));
