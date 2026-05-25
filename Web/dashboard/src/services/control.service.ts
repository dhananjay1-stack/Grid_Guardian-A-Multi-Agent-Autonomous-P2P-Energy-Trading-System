import api from './api';

interface ControlResponse {
  success: boolean;
  message: string;
  state?: {
    trading_enabled: boolean;
    manual_override: boolean;
    safe_mode: boolean;
  };
}

export const controlService = {
  // Enable trading
  async enableTrading(): Promise<ControlResponse> {
    const response = await api.post<ControlResponse>('/api/control/trading/enable');
    return response.data;
  },

  // Disable trading
  async disableTrading(): Promise<ControlResponse> {
    const response = await api.post<ControlResponse>('/api/control/trading/disable');
    return response.data;
  },

  // Set manual override
  async setManualOverride(enabled: boolean): Promise<ControlResponse> {
    const response = await api.post<ControlResponse>('/api/control/manual-override', {
      enabled,
    });
    return response.data;
  },

  // Enable safe mode (emergency stop)
  async enableSafeMode(): Promise<ControlResponse> {
    const response = await api.post<ControlResponse>('/api/control/safe-mode/enable');
    return response.data;
  },

  // Disable safe mode
  async disableSafeMode(): Promise<ControlResponse> {
    const response = await api.post<ControlResponse>('/api/control/safe-mode/disable');
    return response.data;
  },

  // Get current control state
  async getState(): Promise<ControlResponse['state']> {
    const response = await api.get<ControlResponse['state']>('/api/control/state');
    return response.data;
  },

  // Refresh all data
  async refresh(): Promise<{ success: boolean }> {
    const response = await api.post<{ success: boolean }>('/api/system/refresh');
    return response.data;
  },
};

export default controlService;
