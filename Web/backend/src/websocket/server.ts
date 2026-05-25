/**
 * WebSocket Server for real-time communication
 */
import { WebSocketServer as WSServer, WebSocket } from 'ws';
import { Server } from 'http';
import { v4 as uuidv4 } from 'uuid';
import { verifyToken, JwtPayload } from '../middleware/auth';
import { logger } from '../utils/logger';

interface WSClient {
  id: string;
  ws: WebSocket;
  user?: JwtPayload;
  subscriptions: Set<string>;
  lastPing: number;
}

interface WSMessage {
  type: string;
  payload?: unknown;
  timestamp?: string;
}

export class WebSocketServer {
  private wss: WSServer;
  private clients: Map<string, WSClient> = new Map();
  private pingInterval: NodeJS.Timeout | null = null;

  constructor(server: Server) {
    this.wss = new WSServer({ server, path: '/ws' });
    this.setupServer();
    this.startPingInterval();
  }

  private setupServer(): void {
    this.wss.on('connection', (ws, req) => {
      const clientId = uuidv4();
      const client: WSClient = {
        id: clientId,
        ws,
        subscriptions: new Set(),
        lastPing: Date.now(),
      };

      // Authenticate via query parameter token
      const url = new URL(req.url || '', `http://${req.headers.host}`);
      const token = url.searchParams.get('token');

      if (token) {
        try {
          client.user = verifyToken(token);
          logger.info(`WebSocket client authenticated: ${client.user.email}`);
        } catch {
          logger.warn(`WebSocket client failed authentication`);
        }
      }

      this.clients.set(clientId, client);
      logger.info(`WebSocket client connected: ${clientId}`);

      // Send welcome message
      this.send(ws, {
        type: 'connected',
        payload: {
          clientId,
          authenticated: !!client.user,
        },
      });

      // Handle messages
      ws.on('message', (data) => {
        try {
          const message = JSON.parse(data.toString()) as WSMessage;
          this.handleMessage(client, message);
        } catch (error) {
          logger.error('Invalid WebSocket message:', error);
          this.send(ws, { type: 'error', payload: { message: 'Invalid message format' } });
        }
      });

      // Handle pong
      ws.on('pong', () => {
        client.lastPing = Date.now();
      });

      // Handle close
      ws.on('close', () => {
        this.clients.delete(clientId);
        logger.info(`WebSocket client disconnected: ${clientId}`);
      });

      // Handle error
      ws.on('error', (error) => {
        logger.error(`WebSocket error for client ${clientId}:`, error);
        this.clients.delete(clientId);
      });
    });
  }

  private handleMessage(client: WSClient, message: WSMessage): void {
    switch (message.type) {
      case 'subscribe:node':
        if (message.payload && typeof (message.payload as { nodeId: string }).nodeId === 'string') {
          const nodeId = (message.payload as { nodeId: string }).nodeId;
          client.subscriptions.add(`node:${nodeId}`);
          this.send(client.ws, {
            type: 'subscribed',
            payload: { channel: `node:${nodeId}` },
          });
        }
        break;

      case 'unsubscribe:node':
        if (message.payload && typeof (message.payload as { nodeId: string }).nodeId === 'string') {
          const nodeId = (message.payload as { nodeId: string }).nodeId;
          client.subscriptions.delete(`node:${nodeId}`);
          this.send(client.ws, {
            type: 'unsubscribed',
            payload: { channel: `node:${nodeId}` },
          });
        }
        break;

      case 'subscribe:trades':
        client.subscriptions.add('trades');
        this.send(client.ws, {
          type: 'subscribed',
          payload: { channel: 'trades' },
        });
        break;

      case 'unsubscribe:trades':
        client.subscriptions.delete('trades');
        this.send(client.ws, {
          type: 'unsubscribed',
          payload: { channel: 'trades' },
        });
        break;

      case 'ping':
        this.send(client.ws, { type: 'pong' });
        break;

      default:
        this.send(client.ws, {
          type: 'error',
          payload: { message: `Unknown message type: ${message.type}` },
        });
    }
  }

  private send(ws: WebSocket, message: WSMessage): void {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        ...message,
        timestamp: new Date().toISOString(),
      }));
    }
  }

  private startPingInterval(): void {
    this.pingInterval = setInterval(() => {
      const now = Date.now();
      this.clients.forEach((client, clientId) => {
        // Disconnect clients that haven't responded to ping in 60s
        if (now - client.lastPing > 60000) {
          logger.warn(`WebSocket client ${clientId} timed out`);
          client.ws.terminate();
          this.clients.delete(clientId);
          return;
        }
        client.ws.ping();
      });
    }, 30000); // Ping every 30 seconds
  }

  /**
   * Broadcast to all clients subscribed to a channel
   */
  broadcast(channel: string, type: string, payload: unknown): void {
    const message: WSMessage = { type, payload };

    this.clients.forEach((client) => {
      if (client.subscriptions.has(channel) || channel === 'all') {
        this.send(client.ws, message);
      }
    });
  }

  /**
   * Broadcast telemetry update for a specific node
   */
  broadcastTelemetry(nodeId: string, data: unknown): void {
    this.broadcast(`node:${nodeId}`, 'telemetry:update', {
      nodeId,
      data,
      timestamp: new Date().toISOString(),
    });
  }

  /**
   * Broadcast trade event
   */
  broadcastTradeEvent(eventType: string, tradeData: unknown): void {
    this.broadcast('trades', `trade:${eventType}`, tradeData);
  }

  /**
   * Broadcast node status change
   */
  broadcastNodeStatus(nodeId: string, status: string): void {
    this.broadcast(`node:${nodeId}`, 'node:status', { nodeId, status });
    this.broadcast('all', 'node:status', { nodeId, status });
  }

  /**
   * Broadcast alert
   */
  broadcastAlert(alert: { type: string; message: string; severity: string }): void {
    this.broadcast('all', 'alert:new', alert);
  }

  /**
   * Get number of connected clients
   */
  getClientCount(): number {
    return this.clients.size;
  }

  /**
   * Cleanup
   */
  close(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
    }
    this.wss.close();
  }
}
