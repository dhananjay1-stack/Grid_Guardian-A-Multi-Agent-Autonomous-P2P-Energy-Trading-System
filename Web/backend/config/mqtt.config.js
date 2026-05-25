module.exports = {
  broker: process.env.MQTT_BROKER || process.env.MQTT_BROKER_URL || 'mqtt://localhost:1883',
  options: {
    clientId: `gridguardian_backend_${Math.random().toString(16).substr(2, 8)}`,
    clean: true,
    connectTimeout: 4000,
    reconnectPeriod: 5000,
    username: process.env.MQTT_USERNAME || '',
    password: process.env.MQTT_PASSWORD || '',
  },
  topics: {
    telemetry: 'gridguardian/+/telemetry',
    status: 'gridguardian/+/status',
    alerts: 'gridguardian/+/alerts',
  },
  qos: 1,
};
