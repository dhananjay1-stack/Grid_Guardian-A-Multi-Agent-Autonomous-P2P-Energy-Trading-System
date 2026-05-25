/**
 * MQTT to WebSocket Bridge
 *
 * Subscribes to MQTT topics from Pi devices and broadcasts
 * telemetry updates to WebSocket clients.
 */
import mqtt, { MqttClient, IClientOptions } from 'mqtt';
import { config } from '../config/env';
import { logger } from '../utils/logger';
import { WebSocketServer } from './server';

interface TelemetryMessage {
  node_id: string;
  timestamp: string;
  soc_kwh: number;
  soc_capacity_kwh: number;
  pv_gen_kw: number;
  load_kw: number;
  net_kw: number;
  battery_power_kw: number;
  price_signal: number;
  voltage_v?: number;
  current_a?: number;
  action_taken?: number;
}

interface OfferMessage {
  node_id: string;
  offer_hash: string;
  commit_hash: string;
  kwh_bucket: number;
  price_bucket: number;
  round_id: number;
  timestamp: string;
}

export class MqttBridge {
  private client: MqttClient | null = null;
  private wsServer: WebSocketServer;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;

  constructor(wsServer: WebSocketServer) {
    this.wsServer = wsServer;
  }

  async connect(): Promise<void> {
    if (!config.mqttBrokerUrl) {
      logger.warn('MQTT broker URL not configured - bridge disabled');
      return;
    }

    const options: IClientOptions = {
      clientId: `grid-guardian-backend-${process.pid}`,
      clean: true,
      reconnectPeriod: 5000,
      connectTimeout: 30000,
    };

    if (config.mqttUsername) {
      options.username = config.mqttUsername;
      options.password = config.mqttPassword;
    }

    return new Promise((resolve, reject) => {
      this.client = mqtt.connect(config.mqttBrokerUrl, options);

      this.client.on('connect', () => {
        logger.info('MQTT bridge connected');
        this.reconnectAttempts = 0;
        this.subscribeToTopics();
        resolve();
      });

      this.client.on('error', (error) => {
        logger.error('MQTT connection error:', error);
        if (this.reconnectAttempts === 0) {
          reject(error);
        }
      });

      this.client.on('reconnect', () => {
        this.reconnectAttempts++;
        logger.info(`MQTT reconnecting (attempt ${this.reconnectAttempts})`);
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
          logger.error('MQTT max reconnect attempts reached');
          this.client?.end();
        }
      });

      this.client.on('offline', () => {
        logger.warn('MQTT client offline');
      });

      this.client.on('message', (topic, message) => {
        this.handleMessage(topic, message);
      });
    });
  }

  private subscribeToTopics(): void {
    if (!this.client) return;

    const topics = [
      'gridguardian/+/telemetry',  // Individual node telemetry
      'gridguardian/+/offers',     // Node offers
      'gridguardian/+/status',     // Node status changes
      'gridguardian/events',       // Global events
    ];

    topics.forEach((topic) => {
      this.client?.subscribe(topic, { qos: 1 }, (err) => {
        if (err) {
          logger.error(`Failed to subscribe to ${topic}:`, err);
        } else {
          logger.info(`Subscribed to MQTT topic: ${topic}`);
        }
      });
    });
  }

  private handleMessage(topic: string, message: Buffer): void {
    try {
      const data = JSON.parse(message.toString());
      const parts = topic.split('/');

      // gridguardian/<node_id>/telemetry
      if (parts.length === 3 && parts[2] === 'telemetry') {
        const nodeId = parts[1];
        this.handleTelemetry(nodeId, data as TelemetryMessage);
      }

      // gridguardian/<node_id>/offers
      else if (parts.length === 3 && parts[2] === 'offers') {
        const nodeId = parts[1];
        this.handleOffer(nodeId, data as OfferMessage);
      }

      // gridguardian/<node_id>/status
      else if (parts.length === 3 && parts[2] === 'status') {
        const nodeId = parts[1];
        this.handleStatus(nodeId, data);
      }

      // gridguardian/events
      else if (parts.length === 2 && parts[1] === 'events') {
        this.handleEvent(data);
      }

    } catch (error) {
      logger.error(`Failed to parse MQTT message on ${topic}:`, error);
    }
  }

  private handleTelemetry(nodeId: string, data: TelemetryMessage): void {
    logger.debug(`Telemetry from node ${nodeId}`);

    // Broadcast to WebSocket clients
    this.wsServer.broadcastTelemetry(nodeId, data);

    // TODO: Store in database for historical queries
  }

  private handleOffer(nodeId: string, data: OfferMessage): void {
    logger.info(`Offer from node ${nodeId}: ${data.offer_hash}`);

    // Broadcast to trade subscribers
    this.wsServer.broadcastTradeEvent('offer', {
      nodeId,
      ...data,
    });
  }

  private handleStatus(nodeId: string, data: { status: string; timestamp: string }): void {
    logger.info(`Status change for node ${nodeId}: ${data.status}`);

    // Broadcast node status
    this.wsServer.broadcastNodeStatus(nodeId, data.status);

    // TODO: Update node status in database
  }

  private handleEvent(data: { type: string; payload: unknown }): void {
    logger.info(`Global event: ${data.type}`);

    // Broadcast based on event type
    if (data.type === 'alert') {
      this.wsServer.broadcastAlert(data.payload as { type: string; message: string; severity: string });
    }
  }

  /**
   * Publish a command to a specific node
   */
  publishCommand(nodeId: string, command: string, params: unknown): void {
    if (!this.client) {
      logger.error('MQTT client not connected');
      return;
    }

    const topic = `gridguardian/${nodeId}/commands`;
    const message = JSON.stringify({
      command,
      params,
      timestamp: new Date().toISOString(),
    });

    this.client.publish(topic, message, { qos: 1 }, (err) => {
      if (err) {
        logger.error(`Failed to publish command to ${nodeId}:`, err);
      } else {
        logger.info(`Command sent to ${nodeId}: ${command}`);
      }
    });
  }

  /**
   * Disconnect from MQTT broker
   */
  disconnect(): void {
    if (this.client) {
      this.client.end();
      logger.info('MQTT bridge disconnected');
    }
  }
}
