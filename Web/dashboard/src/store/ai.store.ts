import { create } from 'zustand';
import { AIDecisionData, AIDecision } from '@/types';

interface AIState {
  // Latest decision by node
  decisionsByNode: Record<string, AIDecisionData>;
  // Decision history by node
  historyByNode: Record<string, AIDecisionData[]>;
  // Loading states
  isLoading: boolean;
  // Error state
  error: string | null;
  // Actions
  setDecision: (nodeId: string, data: AIDecisionData) => void;
  addToHistory: (nodeId: string, data: AIDecisionData) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  updateFromLive: (data: AIDecisionData) => void;
  clearError: () => void;
}

export const useAIStore = create<AIState>((set, get) => ({
  decisionsByNode: {},
  historyByNode: {},
  isLoading: false,
  error: null,

  setDecision: (nodeId, data) =>
    set((state) => ({
      decisionsByNode: { ...state.decisionsByNode, [nodeId]: data },
    })),

  addToHistory: (nodeId, data) =>
    set((state) => ({
      historyByNode: {
        ...state.historyByNode,
        [nodeId]: [...(state.historyByNode[nodeId] || []), data].slice(-100),
      },
    })),

  setLoading: (loading) => set({ isLoading: loading }),
  setError: (error) => set({ error }),

  updateFromLive: (data) => {
    const { decisionsByNode, historyByNode } = get();
    const nodeId = data.node_id;

    set({
      decisionsByNode: { ...decisionsByNode, [nodeId]: data },
      historyByNode: {
        ...historyByNode,
        [nodeId]: [...(historyByNode[nodeId] || []), data].slice(-100),
      },
    });
  },

  clearError: () => set({ error: null }),
}));
