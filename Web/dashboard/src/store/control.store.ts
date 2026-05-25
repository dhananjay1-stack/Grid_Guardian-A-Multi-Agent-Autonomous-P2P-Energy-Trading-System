import { create } from 'zustand';
import { ControlState } from '@/types';

interface ControlStoreState extends ControlState {
  // Loading states
  isLoading: boolean;
  // Pending actions
  pendingAction: string | null;
  // Error state
  error: string | null;
  // Actions
  setTradingEnabled: (enabled: boolean) => void;
  setManualOverride: (override: boolean) => void;
  setSafeMode: (safeMode: boolean) => void;
  setLastAction: (action: string, success: boolean) => void;
  setPendingAction: (action: string | null) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  clearError: () => void;
}

export const useControlStore = create<ControlStoreState>((set) => ({
  trading_enabled: true,
  manual_override: false,
  safe_mode: false,
  last_action: undefined,
  isLoading: false,
  pendingAction: null,
  error: null,

  setTradingEnabled: (enabled) => set({ trading_enabled: enabled }),

  setManualOverride: (override) => set({ manual_override: override }),

  setSafeMode: (safeMode) => set({ safe_mode: safeMode }),

  setLastAction: (action, success) =>
    set({
      last_action: {
        action,
        timestamp: Date.now(),
        success,
      },
      pendingAction: null,
    }),

  setPendingAction: (action) => set({ pendingAction: action }),

  setLoading: (loading) => set({ isLoading: loading }),
  setError: (error) => set({ error, pendingAction: null }),
  clearError: () => set({ error: null }),
}));
