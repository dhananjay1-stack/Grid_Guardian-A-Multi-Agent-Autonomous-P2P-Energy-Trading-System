import { create } from 'zustand';
import { SystemHealth, DashboardSummary, Alert } from '@/types';

interface SystemState {
  // System health
  health: SystemHealth | null;
  // Dashboard summary
  summary: DashboardSummary | null;
  // Active alerts
  alerts: Alert[];
  // Connection state
  isConnected: boolean;
  lastConnectedAt: number | null;
  // Loading states
  isLoading: boolean;
  // Error state
  error: string | null;
  // Actions
  setHealth: (health: SystemHealth) => void;
  setSummary: (summary: DashboardSummary) => void;
  setAlerts: (alerts: Alert[]) => void;
  addAlert: (alert: Alert) => void;
  acknowledgeAlert: (alertId: string) => void;
  setConnected: (connected: boolean) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  clearError: () => void;
}

export const useSystemStore = create<SystemState>((set) => ({
  health: null,
  summary: null,
  alerts: [],
  isConnected: false,
  lastConnectedAt: null,
  isLoading: false,
  error: null,

  setHealth: (health) => set({ health }),

  setSummary: (summary) => set({ summary }),

  setAlerts: (alerts) => set({ alerts }),

  addAlert: (alert) =>
    set((state) => ({
      alerts: [alert, ...state.alerts].slice(0, 50),
    })),

  acknowledgeAlert: (alertId) =>
    set((state) => ({
      alerts: state.alerts.map((a) =>
        a.id === alertId ? { ...a, acknowledged: true } : a
      ),
    })),

  setConnected: (connected) =>
    set({
      isConnected: connected,
      lastConnectedAt: connected ? Date.now() : null,
    }),

  setLoading: (loading) => set({ isLoading: loading }),
  setError: (error) => set({ error }),
  clearError: () => set({ error: null }),
}));
