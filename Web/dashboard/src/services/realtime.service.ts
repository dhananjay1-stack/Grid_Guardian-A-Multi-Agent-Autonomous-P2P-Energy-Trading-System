import { io, Socket } from 'socket.io-client';
import { useTelemetryStore, useAIStore, useBlockchainStore, useSystemStore } from '@/store';
import { TelemetryData, AIDecisionData, BlockchainEvent, Alert } from '@/types';

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'http://localhost:3000';

class RealtimeService {
  private socket: Socket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private subscribedNodes: Set<string> = new Set();

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.socket?.connected) {
        resolve();
        return;
      }

      this.socket = io(WS_URL, {
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionAttempts: this.maxReconnectAttempts,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 5000,
      });

      this.socket.on('connect', () => {
        console.log('Socket.io connected');
        this.reconnectAttempts = 0;
        useSystemStore.getState().setConnected(true);

        // Re-subscribe to nodes after reconnect
        this.subscribedNodes.forEach((nodeId) => {
          this.socket?.emit('subscribe', nodeId);
        });

        resolve();
      });

      this.socket.on('disconnect', (reason) => {
        console.log('Socket.io disconnected:', reason);
        useSystemStore.getState().setConnected(false);
      });

      this.socket.on('connect_error', (error) => {
        console.error('Socket.io connection error:', error);
        this.reconnectAttempts++;

        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
          useSystemStore.getState().setError('Failed to connect to real-time server');
          reject(error);
        }
      });

      // Set up event listeners
      this.setupListeners();

      // Timeout for initial connection
      setTimeout(() => {
        if (!this.socket?.connected) {
          console.warn('Socket.io connection timeout');
          resolve(); // Don't reject, just continue without real-time
        }
      }, 10000);
    });
  }

  private setupListeners() {
    if (!this.socket) return;

    // Telemetry updates
    this.socket.on('telemetry', (data: { node_id: string; data: TelemetryData }) => {
      try {
        useTelemetryStore.getState().updateFromLive(data.data || data as unknown as TelemetryData);
      } catch (error) {
        console.error('Error processing telemetry event:', error);
      }
    });

    // Node-specific telemetry
    this.socket.on('telemetry:node', (data: TelemetryData) => {
      try {
        useTelemetryStore.getState().updateFromLive(data);
      } catch (error) {
        console.error('Error processing node telemetry event:', error);
      }
    });

    // AI decision updates
    this.socket.on('ai:decision', (data: AIDecisionData) => {
      try {
        useAIStore.getState().updateFromLive(data);
      } catch (error) {
        console.error('Error processing AI decision event:', error);
      }
    });

    // Blockchain trade updates
    this.socket.on('blockchain:trade', (data: BlockchainEvent) => {
      try {
        useBlockchainStore.getState().updateFromLive(data);
      } catch (error) {
        console.error('Error processing blockchain event:', error);
      }
    });

    // System status updates
    this.socket.on('system:status', (data: { status: string }) => {
      console.log('System status update:', data);
    });

    // Alert updates
    this.socket.on('alert', (data: Alert) => {
      try {
        useSystemStore.getState().addAlert(data);
      } catch (error) {
        console.error('Error processing alert event:', error);
      }
    });

    // Status updates
    this.socket.on('status', (data: unknown) => {
      console.log('Status update:', data);
    });
  }

  subscribe(nodeId: string) {
    if (this.socket?.connected) {
      this.socket.emit('subscribe', nodeId);
    }
    this.subscribedNodes.add(nodeId);
  }

  unsubscribe(nodeId: string) {
    if (this.socket?.connected) {
      this.socket.emit('unsubscribe', nodeId);
    }
    this.subscribedNodes.delete(nodeId);
  }

  subscribeToTrades() {
    if (this.socket?.connected) {
      this.socket.emit('subscribe:trades');
    }
  }

  disconnect() {
    if (this.socket) {
      this.socket.disconnect();
      this.socket = null;
      this.subscribedNodes.clear();
      useSystemStore.getState().setConnected(false);
    }
  }

  isConnected(): boolean {
    return this.socket?.connected || false;
  }
}

export const realtimeService = new RealtimeService();
export default realtimeService;
