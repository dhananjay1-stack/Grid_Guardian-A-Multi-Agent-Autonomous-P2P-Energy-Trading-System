/**
 * Simulation Service
 *
 * Client-side service for interacting with the P2P trading simulation API.
 */

import api from './api';

export interface ProsumerState {
  prosumer_id: string;
  name: string;
  current_hour: number;
  solar_generation_kw: number;
  load_demand_kw: number;
  net_power_kw: number;
  surplus_energy_kwh: number;
  deficit_energy_kwh: number;
  battery: {
    soc: number;
    soc_kwh: number;
    soc_percent: number;
    capacity_kwh: number;
    current_power_kw: number;
    is_charging: boolean;
    is_discharging: boolean;
    available_charge_kwh: number;
    available_discharge_kwh: number;
  };
  trade_status: string;
  current_offer: any | null;
  current_bid: any | null;
  current_mode: string;
  total_solar_generated: number;
  total_load_consumed: number;
  total_grid_import: number;
  total_grid_export: number;
  total_p2p_sold: number;
  total_p2p_bought: number;
}

export interface MarketState {
  round_id: number;
  current_price: number;
  grid_buy_price: number;
  grid_sell_price: number;
  open_offers: number;
  open_bids: number;
  total_offer_volume: number;
  total_bid_volume: number;
  pending_trades: number;
  stats: {
    total_offers: number;
    total_bids: number;
    total_matches: number;
    total_volume_kwh: number;
    total_value: number;
  };
}

export interface Trade {
  trade_id: string;
  seller_id: string;
  seller_name: string;
  buyer_id: string;
  buyer_name: string;
  quantity_kwh: number;
  price_per_kwh: number;
  total_price: number;
  status: string;
  matched_at: number;
  blockchain_tx_hash?: string;
}

export interface SimulationState {
  is_running: boolean;
  tick_count: number;
  simulation_hour: number;
  simulation_day: number;
  prosumer_count: number;
  prosumers: ProsumerState[];
  market: MarketState;
  order_book: {
    offers: Array<{ price: number; quantity: number; prosumer: string }>;
    bids: Array<{ price: number; quantity: number; prosumer: string }>;
  };
  recent_trades: Trade[];
  stats: {
    ticks: number;
    total_solar_kwh: number;
    total_load_kwh: number;
    total_p2p_volume: number;
    total_grid_import: number;
    total_grid_export: number;
  };
}

const simulationService = {
  /**
   * Initialize simulation
   */
  async initialize(prosumers?: any[]): Promise<any> {
    const response = await api.post('/simulation/initialize', { prosumers });
    return response.data;
  },

  /**
   * Start simulation
   */
  async start(speed?: number): Promise<any> {
    const response = await api.post('/simulation/start', { speed });
    return response.data;
  },

  /**
   * Stop simulation
   */
  async stop(): Promise<any> {
    const response = await api.post('/simulation/stop');
    return response.data;
  },

  /**
   * Run single simulation step
   */
  async step(): Promise<any> {
    const response = await api.post('/simulation/step');
    return response.data;
  },

  /**
   * Get simulation state
   */
  async getState(): Promise<SimulationState> {
    const response = await api.get('/simulation/state');
    return (response.data as any).data;
  },

  /**
   * Reset simulation
   */
  async reset(): Promise<any> {
    const response = await api.post('/simulation/reset');
    return response.data;
  },

  /**
   * Set simulation speed
   */
  async setSpeed(speed: number): Promise<any> {
    const response = await api.post('/simulation/speed', { speed });
    return response.data;
  },

  /**
   * Set simulation time
   */
  async setTime(hour: number): Promise<any> {
    const response = await api.post('/simulation/time', { hour });
    return response.data;
  },

  /**
   * Get all prosumers
   */
  async getProsumers(): Promise<ProsumerState[]> {
    const response = await api.get('/simulation/prosumers');
    return (response.data as any).data;
  },

  /**
   * Get single prosumer
   */
  async getProsumer(id: string): Promise<ProsumerState> {
    const response = await api.get(`/simulation/prosumers/${id}`);
    return (response.data as any).data;
  },

  /**
   * Add prosumer
   */
  async addProsumer(id: string, name: string, config?: any): Promise<any> {
    const response = await api.post('/simulation/prosumers', { id, name, config });
    return response.data;
  },

  /**
   * Remove prosumer
   */
  async removeProsumer(id: string): Promise<any> {
    const response = await api.delete(`/simulation/prosumers/${id}`);
    return response.data;
  },

  /**
   * Get market state
   */
  async getMarket(): Promise<{ market: MarketState; order_book: any }> {
    const response = await api.get('/simulation/market');
    return (response.data as any).data;
  },

  /**
   * Get order book
   */
  async getOrderBook(): Promise<any> {
    const response = await api.get('/simulation/market/orderbook');
    return (response.data as any).data;
  },

  /**
   * Get recent trades
   */
  async getTrades(limit?: number): Promise<Trade[]> {
    const response = await api.get('/simulation/market/trades', {
      params: { limit },
    });
    return (response.data as any).data;
  },

  /**
   * Submit sell offer
   */
  async submitOffer(prosumer_id: string, quantity_kwh: number, price_per_kwh: number): Promise<any> {
    const response = await api.post('/simulation/market/offer', {
      prosumer_id,
      quantity_kwh,
      price_per_kwh,
    });
    return response.data;
  },

  /**
   * Submit buy bid
   */
  async submitBid(prosumer_id: string, quantity_kwh: number, max_price_per_kwh: number): Promise<any> {
    const response = await api.post('/simulation/market/bid', {
      prosumer_id,
      quantity_kwh,
      max_price_per_kwh,
    });
    return response.data;
  },

  /**
   * Cancel order
   */
  async cancelOrder(order_id: string): Promise<any> {
    const response = await api.delete(`/simulation/market/order/${order_id}`);
    return response.data;
  },

  /**
   * Run market matching
   */
  async runMatching(): Promise<any> {
    const response = await api.post('/simulation/market/match');
    return response.data;
  },
};

export default simulationService;
