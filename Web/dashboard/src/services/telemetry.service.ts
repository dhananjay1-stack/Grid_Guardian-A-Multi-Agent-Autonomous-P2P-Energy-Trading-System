import api from './api';
import { TelemetryData } from '@/types';

export const telemetryService = {
  // Get latest telemetry for a node
  async getLatest(nodeId: string): Promise<TelemetryData> {
    const response = await api.get<TelemetryData>(`/api/telemetry/latest/${nodeId}`);
    return response.data;
  },

  // Get telemetry history for a node
  async getHistory(nodeId: string, hours: number = 1): Promise<TelemetryData[]> {
    const response = await api.get<TelemetryData[]>(
      `/api/telemetry/history/${nodeId}?hours=${hours}`
    );
    return response.data;
  },

  // Submit telemetry data (for testing)
  async submit(data: Omit<TelemetryData, '_id'>): Promise<TelemetryData> {
    const response = await api.post<TelemetryData>('/api/telemetry', data);
    return response.data;
  },

  // Get all latest telemetry (for dashboard summary)
  async getAllLatest(): Promise<TelemetryData[]> {
    const response = await api.get<TelemetryData[]>('/api/telemetry/latest');
    return response.data;
  },
};

export default telemetryService;
