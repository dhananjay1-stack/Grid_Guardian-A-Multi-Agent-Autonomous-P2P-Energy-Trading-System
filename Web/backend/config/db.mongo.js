const mongoose = require('mongoose');
const logger = require('../utils/logger');

let isConnected = false;

const connectMongo = async () => {
  // Skip MongoDB if explicitly disabled
  if (process.env.MONGO_ENABLED === 'false') {
    logger.warn('MongoDB disabled via MONGO_ENABLED=false');
    return null;
  }

  try {
    const mongoUri = process.env.MONGO_URI || process.env.MONGODB_URI || 'mongodb://localhost:27017/gridguardian';

    await mongoose.connect(mongoUri, {
      useNewUrlParser: true,
      useUnifiedTopology: true,
    });

    isConnected = true;
    logger.info('MongoDB connected successfully');

    mongoose.connection.on('error', (err) => {
      logger.error('MongoDB connection error:', err);
    });

    mongoose.connection.on('disconnected', () => {
      isConnected = false;
      logger.warn('MongoDB disconnected');
    });

    mongoose.connection.on('reconnected', () => {
      isConnected = true;
      logger.info('MongoDB reconnected');
    });

    return mongoose.connection;
  } catch (error) {
    logger.warn('MongoDB connection failed (running in degraded mode):', error.message);
    logger.warn('Telemetry storage will be unavailable');
    isConnected = false;
    // Don't throw - allow backend to run without MongoDB
    return null;
  }
};

const disconnectMongo = async () => {
  try {
    await mongoose.connection.close();
    logger.info('MongoDB connection closed');
  } catch (error) {
    logger.error('Error closing MongoDB connection:', error.message);
    throw error;
  }
};

const isMongoConnected = () => isConnected;

module.exports = { connectMongo, disconnectMongo, isMongoConnected };
