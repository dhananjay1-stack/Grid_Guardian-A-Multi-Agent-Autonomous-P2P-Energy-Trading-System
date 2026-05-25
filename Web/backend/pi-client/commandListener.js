/**
 * Grid-Guardian Pi Command Listener
 * Edge Node MQTT Client for receiving commands from backend
 *
 * This is an example implementation for Raspberry Pi edge nodes.
 * Install on Pi: npm install mqtt dotenv ethers
 */

const mqtt = require('mqtt');
const { ethers } = require('ethers');
const crypto = require('crypto');

// Configuration (use environment variables in production)
const config = {
  mqtt: {
    broker: process.env.MQTT_BROKER_URL || 'mqtt://localhost:1883',
    username: process.env.MQTT_USERNAME || '',
    password: process.env.MQTT_PASSWORD || '',
  },
  nodeId: process.env.NODE_ID || 'NODE-PI-001',
  privateKey: process.env.PI_PRIVATE_KEY || '', // EIP-712 signing key
  safetyLimits: {
    maxDischargeKwh: 10,
    maxChargeKwh: 10,
    minVoltage: 200,
    maxVoltage: 260,
    minSoc: 20, // State of charge %
    maxSoc: 95,
  },
};

// State
let wallet = null;
let safeMode = false;
let currentSoc = 50; // Mock state of charge
let lastMeterReading = 0;

/**
 * Initialize wallet for EIP-712 signing
 */
function initializeWallet() {
  if (config.privateKey) {
    wallet = new ethers.Wallet(config.privateKey);
    console.log(`Wallet initialized: ${wallet.address}`);
  } else {
    console.warn('No private key configured - receipt signing disabled');
  }
}

/**
 * Safety Shield - validates commands before execution
 */
function checkSafety(command) {
  const { command_type, payload } = command;

  // Always allow safe_mode and heartbeat
  if (command_type === 'safe_mode' || command_type === 'heartbeat_check') {
    return { safe: true };
  }

  // If in safe mode, reject all other commands
  if (safeMode) {
    return { safe: false, reason: 'Device is in safe mode' };
  }

  // Check specific command types
  switch (command_type) {
    case 'discharge':
      if (payload.kwh_amount > config.safetyLimits.maxDischargeKwh) {
        return { safe: false, reason: `Discharge amount exceeds limit: ${config.safetyLimits.maxDischargeKwh} kWh` };
      }
      if (currentSoc < config.safetyLimits.minSoc) {
        return { safe: false, reason: `SoC too low: ${currentSoc}%` };
      }
      break;

    case 'charge':
      if (payload.kwh_amount > config.safetyLimits.maxChargeKwh) {
        return { safe: false, reason: `Charge amount exceeds limit: ${config.safetyLimits.maxChargeKwh} kWh` };
      }
      if (currentSoc > config.safetyLimits.maxSoc) {
        return { safe: false, reason: `SoC too high: ${currentSoc}%` };
      }
      break;

    case 'execute_trade':
      if (!payload.trade_id) {
        return { safe: false, reason: 'Missing trade_id' };
      }
      if (!payload.safety_flag) {
        return { safe: false, reason: 'Safety flag not set' };
      }
      break;
  }

  return { safe: true };
}

/**
 * Execute a command after safety validation
 */
async function executeCommand(command, mqttClient) {
  const { command_id, command_type, payload } = command;

  console.log(`Executing command: ${command_type} (${command_id})`);

  switch (command_type) {
    case 'execute_trade':
      await handleExecuteTrade(command, mqttClient);
      break;

    case 'discharge':
      await handleDischarge(command, mqttClient);
      break;

    case 'charge':
      await handleCharge(command, mqttClient);
      break;

    case 'safe_mode':
      handleSafeMode(command, mqttClient);
      break;

    case 'hold':
      handleHold(command, mqttClient);
      break;

    case 'post_receipt':
      await handlePostReceipt(command, mqttClient);
      break;

    case 'heartbeat_check':
      handleHeartbeat(command, mqttClient);
      break;

    case 'settlement_complete':
      handleSettlementComplete(command, mqttClient);
      break;

    case 'delivery_confirmed':
      handleDeliveryConfirmed(command, mqttClient);
      break;

    default:
      console.warn(`Unknown command type: ${command_type}`);
      sendStatus(mqttClient, 'command_unknown', { command_id, command_type });
  }
}

/**
 * Handle execute_trade command
 */
async function handleExecuteTrade(command, mqttClient) {
  const { payload } = command;
  const action = payload.action || 'discharge';

  console.log(`Executing trade ${payload.trade_id}: ${action} ${payload.kwh_bucket * 0.1} kWh`);

  // Simulate energy transfer
  if (action === 'discharge') {
    currentSoc -= Math.min((payload.kwh_bucket * 0.1 / 10) * 100, currentSoc - config.safetyLimits.minSoc);
  } else if (action === 'charge') {
    currentSoc += Math.min((payload.kwh_bucket * 0.1 / 10) * 100, config.safetyLimits.maxSoc - currentSoc);
  }

  lastMeterReading += payload.kwh_bucket * 0.1;

  // Send acknowledgement
  sendStatus(mqttClient, 'trade_executed', {
    trade_id: payload.trade_id,
    action,
    kwh_bucket: payload.kwh_bucket,
    new_soc: currentSoc,
    meter_reading: lastMeterReading,
  });

  // Auto-generate receipt after energy transfer
  if (payload.auto_receipt !== false) {
    setTimeout(() => {
      generateAndSendReceipt(payload.trade_id, payload.kwh_bucket, mqttClient);
    }, 5000); // Wait 5 seconds then send receipt
  }
}

/**
 * Handle discharge command
 */
async function handleDischarge(command, mqttClient) {
  const { payload } = command;

  console.log(`Discharging: ${payload.kwh_amount} kWh`);
  currentSoc -= Math.min((payload.kwh_amount / 10) * 100, currentSoc - config.safetyLimits.minSoc);
  lastMeterReading += payload.kwh_amount;

  sendStatus(mqttClient, 'discharge_complete', {
    trade_id: payload.trade_id,
    kwh_amount: payload.kwh_amount,
    new_soc: currentSoc,
  });
}

/**
 * Handle charge command
 */
async function handleCharge(command, mqttClient) {
  const { payload } = command;

  console.log(`Charging: ${payload.kwh_amount} kWh`);
  currentSoc += Math.min((payload.kwh_amount / 10) * 100, config.safetyLimits.maxSoc - currentSoc);

  sendStatus(mqttClient, 'charge_complete', {
    trade_id: payload.trade_id,
    kwh_amount: payload.kwh_amount,
    new_soc: currentSoc,
  });
}

/**
 * Handle safe_mode command (emergency stop)
 */
function handleSafeMode(command, mqttClient) {
  safeMode = true;
  console.log(`SAFE MODE ACTIVATED: ${command.payload.reason}`);

  sendStatus(mqttClient, 'safe_mode_activated', {
    reason: command.payload.reason,
    timestamp: Date.now(),
  });

  sendAlert(mqttClient, 'SAFE_MODE', {
    message: 'Device entered safe mode',
    reason: command.payload.reason,
    severity: 'high',
  });
}

/**
 * Handle hold command
 */
function handleHold(command, mqttClient) {
  const duration = command.payload.duration_ms || 60000;
  console.log(`Hold command received - pausing for ${duration}ms`);

  sendStatus(mqttClient, 'hold_active', {
    duration_ms: duration,
    reason: command.payload.reason,
  });

  setTimeout(() => {
    console.log('Hold period ended');
    sendStatus(mqttClient, 'hold_complete', {});
  }, duration);
}

/**
 * Handle post_receipt command - generate and sign delivery receipt
 */
async function handlePostReceipt(command, mqttClient) {
  const { trade_id, period_start, period_end } = command.payload;

  await generateAndSendReceipt(
    trade_id,
    Math.round(lastMeterReading * 10), // Convert to bucket
    mqttClient,
    period_start,
    period_end
  );
}

/**
 * Generate and send signed delivery receipt
 */
async function generateAndSendReceipt(tradeId, deliveredKwhBucket, mqttClient, periodStart = null, periodEnd = null) {
  if (!wallet) {
    console.error('Cannot sign receipt - no wallet configured');
    return;
  }

  const now = Date.now();
  const pStart = periodStart || (now - 900000); // 15 minutes ago
  const pEnd = periodEnd || now;

  // Create meter snapshot hash
  const meterSnapshotHash = crypto.createHash('sha256')
    .update(`${lastMeterReading}:${pEnd}:${config.nodeId}`)
    .digest('hex');

  // Get nonce (in production, fetch from backend API)
  const nonce = 0; // Placeholder

  // Create EIP-712 typed data
  const domain = {
    name: 'GridGuardian-Delivery',
    version: '1',
    chainId: parseInt(process.env.CHAIN_ID || '31337', 10),
    verifyingContract: process.env.DELIVERY_REGISTRY_ADDRESS || ethers.ZeroAddress,
  };

  const types = {
    DeliveryReceipt: [
      { name: 'tradeId', type: 'bytes32' },
      { name: 'nodeId', type: 'bytes32' },
      { name: 'meterSnapshotHash', type: 'bytes32' },
      { name: 'deliveredKwhBucket', type: 'uint16' },
      { name: 'periodStart', type: 'uint256' },
      { name: 'periodEnd', type: 'uint256' },
      { name: 'nonce', type: 'uint256' },
    ],
  };

  const value = {
    tradeId,
    nodeId: config.nodeId,
    meterSnapshotHash: '0x' + meterSnapshotHash,
    deliveredKwhBucket,
    periodStart: pStart,
    periodEnd: pEnd,
    nonce,
  };

  try {
    const signature = await wallet.signTypedData(domain, types, value);

    // Send receipt via MQTT
    const receiptTopic = `gridguardian/${config.nodeId}/receipt`;
    mqttClient.publish(receiptTopic, JSON.stringify({
      trade_id: tradeId,
      node_id: config.nodeId,
      meter_reading: lastMeterReading,
      meter_snapshot_hash: '0x' + meterSnapshotHash,
      delivered_kwh_bucket: deliveredKwhBucket,
      period_start: pStart,
      period_end: pEnd,
      nonce,
      signature,
      timestamp: Date.now(),
    }));

    console.log(`Receipt sent for trade ${tradeId}`);
    sendStatus(mqttClient, 'receipt_sent', { trade_id: tradeId });
  } catch (error) {
    console.error('Error signing receipt:', error);
    sendAlert(mqttClient, 'RECEIPT_ERROR', {
      trade_id: tradeId,
      error: error.message,
    });
  }
}

/**
 * Handle heartbeat check
 */
function handleHeartbeat(command, mqttClient) {
  sendStatus(mqttClient, 'heartbeat_ack', {
    command_id: command.command_id,
    soc: currentSoc,
    safe_mode: safeMode,
    meter_reading: lastMeterReading,
    uptime: process.uptime(),
    timestamp: Date.now(),
  });
}

/**
 * Handle settlement complete notification
 */
function handleSettlementComplete(command, mqttClient) {
  console.log(`Settlement complete: ${command.payload.trade_id} - ${command.payload.status}`);
  sendStatus(mqttClient, 'settlement_ack', {
    trade_id: command.payload.trade_id,
    status: command.payload.status,
  });
}

/**
 * Handle delivery confirmed notification
 */
function handleDeliveryConfirmed(command, mqttClient) {
  console.log(`Delivery confirmed: ${command.payload.trade_id}`);
  sendStatus(mqttClient, 'delivery_confirmed_ack', {
    trade_id: command.payload.trade_id,
  });
}

/**
 * Send status message back to backend
 */
function sendStatus(mqttClient, statusType, data) {
  const statusTopic = `gridguardian/${config.nodeId}/status`;
  mqttClient.publish(statusTopic, JSON.stringify({
    status_type: statusType,
    node_id: config.nodeId,
    data,
    timestamp: Date.now(),
  }));
}

/**
 * Send alert message to backend
 */
function sendAlert(mqttClient, alertType, data) {
  const alertTopic = `gridguardian/${config.nodeId}/alerts`;
  mqttClient.publish(alertTopic, JSON.stringify({
    alert_type: alertType,
    node_id: config.nodeId,
    data,
    timestamp: Date.now(),
  }));
}

/**
 * Main entry point
 */
function main() {
  console.log('Grid-Guardian Pi Command Listener starting...');
  console.log(`Node ID: ${config.nodeId}`);

  // Initialize wallet
  initializeWallet();

  // Connect to MQTT broker
  const mqttClient = mqtt.connect(config.mqtt.broker, {
    clientId: `pi_${config.nodeId}_${Date.now()}`,
    username: config.mqtt.username,
    password: config.mqtt.password,
    clean: true,
    reconnectPeriod: 5000,
  });

  mqttClient.on('connect', () => {
    console.log(`Connected to MQTT broker: ${config.mqtt.broker}`);

    // Subscribe to command topic
    const commandTopic = `gridguardian/${config.nodeId}/commands`;
    mqttClient.subscribe(commandTopic, { qos: 1 }, (err) => {
      if (err) {
        console.error(`Failed to subscribe to ${commandTopic}:`, err);
      } else {
        console.log(`Subscribed to: ${commandTopic}`);
      }
    });

    // Send initial status
    sendStatus(mqttClient, 'online', {
      soc: currentSoc,
      safe_mode: safeMode,
      version: '1.0.0',
    });
  });

  mqttClient.on('message', (topic, message) => {
    try {
      const command = JSON.parse(message.toString());
      console.log(`Command received: ${command.command_type}`);

      // Validate command structure
      if (!command.command_id || !command.command_type) {
        console.warn('Invalid command structure');
        return;
      }

      // Check expiry
      if (command.timestamp && command.ttl_ms) {
        if (Date.now() > command.timestamp + command.ttl_ms) {
          console.warn(`Command expired: ${command.command_id}`);
          sendStatus(mqttClient, 'command_expired', { command_id: command.command_id });
          return;
        }
      }

      // Safety check
      const safetyResult = checkSafety(command);
      if (!safetyResult.safe) {
        console.warn(`Safety check failed: ${safetyResult.reason}`);
        sendStatus(mqttClient, 'command_rejected', {
          command_id: command.command_id,
          reason: safetyResult.reason,
        });
        sendAlert(mqttClient, 'SAFETY_REJECTION', {
          command_type: command.command_type,
          reason: safetyResult.reason,
        });
        return;
      }

      // Execute command
      executeCommand(command, mqttClient);
    } catch (error) {
      console.error('Error processing command:', error);
    }
  });

  mqttClient.on('error', (error) => {
    console.error('MQTT error:', error);
  });

  mqttClient.on('close', () => {
    console.log('MQTT connection closed');
  });

  mqttClient.on('reconnect', () => {
    console.log('Reconnecting to MQTT broker...');
  });

  // Periodic telemetry
  setInterval(() => {
    if (mqttClient.connected) {
      const telemetryTopic = `gridguardian/${config.nodeId}/telemetry`;
      mqttClient.publish(telemetryTopic, JSON.stringify({
        node_id: config.nodeId,
        voltage: 230 + Math.random() * 10 - 5,
        current: 5 + Math.random() * 2,
        power: 1000 + Math.random() * 200,
        soc: currentSoc,
        timestamp: Date.now(),
      }));
    }
  }, 15000); // Every 15 seconds

  // Graceful shutdown
  process.on('SIGINT', () => {
    console.log('Shutting down...');
    sendStatus(mqttClient, 'offline', { reason: 'shutdown' });
    setTimeout(() => {
      mqttClient.end();
      process.exit(0);
    }, 1000);
  });
}

// Run
main();
