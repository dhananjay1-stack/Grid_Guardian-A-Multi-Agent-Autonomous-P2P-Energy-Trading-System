'use client';

import { useEffect, useRef, useCallback } from 'react';
import { telemetryService, aiService, blockchainService, realtimeService } from '@/services';
import api from '@/services/api';
import {
  useTelemetryStore,
  useAIStore,
  useBlockchainStore,
  useSystemStore,
} from '@/store';
import { SystemHealth, DashboardSummary } from '@/types';

interface UseDashboardDataOptions {
  nodeId: string;
  autoRefresh?: boolean;
  refreshInterval?: number;
}

export function useDashboardData({
  nodeId,
  autoRefresh = true,
  refreshInterval = 30000,
}: UseDashboardDataOptions) {
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  const {
    setLatestTelemetry,
    setHistory,
    setLoading: setTelemetryLoading,
    setError: setTelemetryError,
  } = useTelemetryStore();

  const {
    setDecision,
    setLoading: setAILoading,
    setError: setAIError,
  } = useAIStore();

  const {
    setTrades,
    setLoading: setBlockchainLoading,
    setError: setBlockchainError,
  } = useBlockchainStore();

  const {
    setHealth,
    setSummary,
    setLoading: setSystemLoading,
    setError: setSystemError,
  } = useSystemStore();

  const fetchTelemetry = useCallback(async () => {
    try {
      setTelemetryLoading(true);
      const data = await telemetryService.getLatest(nodeId);
      setLatestTelemetry(nodeId, data);
    } catch (error) {
      setTelemetryError('Failed to fetch telemetry');
    } finally {
      setTelemetryLoading(false);
    }
  }, [nodeId, setLatestTelemetry, setTelemetryLoading, setTelemetryError]);

  const fetchHistory = useCallback(async (hours: 1 | 24 = 1) => {
    try {
      const data = await telemetryService.getHistory(nodeId, hours);
      setHistory(nodeId, hours === 1 ? '1h' : '24h', data);
    } catch (error) {
      console.warn(`Failed to fetch ${hours}h history`);
    }
  }, [nodeId, setHistory]);

  const fetchAIDecision = useCallback(async () => {
    try {
      setAILoading(true);
      const data = await aiService.getDecision(nodeId);
      setDecision(nodeId, data);
    } catch (error) {
      setAIError('Failed to fetch AI decision');
    } finally {
      setAILoading(false);
    }
  }, [nodeId, setDecision, setAILoading, setAIError]);

  const fetchTrades = useCallback(async () => {
    try {
      setBlockchainLoading(true);
      const data = await blockchainService.getTrades(50);
      setTrades(data);
    } catch (error) {
      setBlockchainError('Failed to fetch trades');
    } finally {
      setBlockchainLoading(false);
    }
  }, [setTrades, setBlockchainLoading, setBlockchainError]);

  const fetchHealth = useCallback(async () => {
    try {
      setSystemLoading(true);
      const response = await api.get<SystemHealth>('/api/system/health');
      setHealth(response.data);
    } catch (error) {
      setSystemError('Failed to fetch system health');
    } finally {
      setSystemLoading(false);
    }
  }, [setHealth, setSystemLoading, setSystemError]);

  const fetchSummary = useCallback(async () => {
    try {
      const response = await api.get<DashboardSummary>('/api/dashboard/summary');
      setSummary(response.data);
    } catch (error) {
      console.warn('Failed to fetch dashboard summary');
    }
  }, [setSummary]);

  const fetchAll = useCallback(async () => {
    await Promise.allSettled([
      fetchTelemetry(),
      fetchHistory(1),
      fetchHistory(24),
      fetchAIDecision(),
      fetchTrades(),
      fetchHealth(),
      fetchSummary(),
    ]);
  }, [fetchTelemetry, fetchHistory, fetchAIDecision, fetchTrades, fetchHealth, fetchSummary]);

  // Connect to real-time updates
  useEffect(() => {
    const connect = async () => {
      try {
        await realtimeService.connect();
        realtimeService.subscribe(nodeId);
        realtimeService.subscribeToTrades();
      } catch (error) {
        console.warn('Real-time connection failed');
      }
    };

    connect();

    return () => {
      realtimeService.unsubscribe(nodeId);
    };
  }, [nodeId]);

  // Auto-refresh
  useEffect(() => {
    if (autoRefresh) {
      intervalRef.current = setInterval(fetchTelemetry, refreshInterval);
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [autoRefresh, refreshInterval, fetchTelemetry]);

  return {
    fetchAll,
    fetchTelemetry,
    fetchHistory,
    fetchAIDecision,
    fetchTrades,
    fetchHealth,
    fetchSummary,
  };
}
