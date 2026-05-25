const mqtt = require('mqtt');
const mqttConfig = require('../config/mqtt.config');
const logger = require('./logger');
const EventEmitter = require('events');

class MQTTClient extends EventEmitter {
  constructor() {
    super();
    this.client = null;
    this.isConnected = false;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 2;
    this.disabled = false;
  }

  /**
   * Connect to MQTT broker
   */
  connect() {
    return new Promise((resolve) => {
      // Skip MQTT if explicitly disabled
      if (process.env.MQTT_ENABLED === 'false') {
        logger.warn('MQTT disabled via MQTT_ENABLED=false');
        this.disabled = true;
        resolve(null);
        return;
      }

      try {
        // Set shorter timeouts for faster failure
        const options = {
          ...mqttConfig.options,
          reconnectPeriod: 1000,
          connectTimeout: 2000,
        };

        this.client = mqtt.connect(mqttConfig.broker, options);

        this.client.on('connect', () => {
          this.isConnected = true;
          this.reconnectAttempts = 0;
          logger.info(`MQTT connected to ${mqttConfig.broker}`);
          this.subscribeToTopics();
          resolve(this.client);
        });

        this.client.on('error', (error) => {
          // Suppress verbose error logging
          logger.debug('MQTT connection issue:', error.message);
          this.emit('error', error);
        });

        this.client.on('close', () => {
          this.isConnected = false;
        });

        this.client.on('reconnect', () => {
          this.reconnectAttempts++;
          if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            logger.warn('MQTT unavailable - continuing without MQTT');
            this.client.end(true);
            this.disabled = true;
            resolve(null);
          }
        });

        this.client.on('offline', () => {
          // Silent
        });

        this.client.on('message', (topic, message) => {
          this.handleMessage(topic, message);
        });

        // Short timeout (2 seconds)
        setTimeout(() => {
          if (!this.isConnected && !this.disabled) {
            logger.warn('MQTT timeout - continuing without MQTT');
            if (this.client) {
              this.client.end(true);
            }
            this.disabled = true;
            resolve(null);
          }
        }, 2000);
      } catch (error) {
        logger.warn('MQTT unavailable - continuing without MQTT');
        this.disabled = true;
        resolve(null);
      }
    });
  }

  /**
   * Subscribe to configured topics
   */
  subscribeToTopics() {
    const topics = Object.values(mqttConfig.topics);

    topics.forEach((topic) => {
      this.client.subscribe(topic, { qos: mqttConfig.qos }, (error) => {
        if (error) {
          logger.error(`Failed to subscribe to ${topic}:`, error);
        } else {
          logger.info(`Subscribed to topic: ${topic}`);
        }
      });
    });
  }

  /**
   * Handle incoming MQTT messages
   */
  handleMessage(topic, message) {
    try {
      // Parse node_id from topic (gridguardian/{node_id}/{type})
      const topicParts = topic.split('/');
      if (topicParts.length < 3) {
        logger.warn(`Invalid topic format: ${topic}`);
        return;
      }

      const nodeId = topicParts[1];
      const messageType = topicParts[2];

      // Parse JSON payload
      let payload;
      try {
        payload = JSON.parse(message.toString());
      } catch (parseError) {
        logger.error(`Malformed JSON from ${topic}:`, parseError.message);
        this.emit('malformed', { topic, message: message.toString() });
        return;
      }

      // Ensure node_id is set
      payload.node_id = payload.node_id || nodeId;

      // Emit appropriate event based on message type
      switch (messageType) {
        case 'telemetry':
          this.emit('telemetry', payload);
          break;
        case 'status':
          this.emit('status', payload);
          break;
        case 'alerts':
          this.emit('alert', payload);
          break;
        default:
          logger.debug(`Unknown message type: ${messageType}`);
          this.emit('message', { type: messageType, payload });
      }
    } catch (error) {
      logger.error('Error handling MQTT message:', error);
    }
  }

  /**
   * Publish a message to a topic
   */
  publish(topic, payload) {
    return new Promise((resolve, reject) => {
      if (!this.isConnected) {
        reject(new Error('MQTT client not connected'));
        return;
      }

      const message = typeof payload === 'string' ? payload : JSON.stringify(payload);

      this.client.publish(topic, message, { qos: mqttConfig.qos }, (error) => {
        if (error) {
          logger.error(`Failed to publish to ${topic}:`, error);
          reject(error);
        } else {
          logger.debug(`Published to ${topic}`);
          resolve();
        }
      });
    });
  }

  /**
   * Disconnect from MQTT broker
   */
  disconnect() {
    return new Promise((resolve) => {
      if (this.client) {
        this.client.end(true, {}, () => {
          logger.info('MQTT disconnected');
          this.isConnected = false;
          resolve();
        });
      } else {
        resolve();
      }
    });
  }

  /**
   * Get connection status
   */
  getStatus() {
    return {
      connected: this.isConnected,
      broker: mqttConfig.broker,
      reconnectAttempts: this.reconnectAttempts,
    };
  }
}

// Export singleton instance
const mqttClient = new MQTTClient();
module.exports = mqttClient;
