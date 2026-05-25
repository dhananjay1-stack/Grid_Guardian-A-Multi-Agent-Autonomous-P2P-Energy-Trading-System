/**
 * API Key authentication middleware for Pi devices
 */
import { Request, Response, NextFunction } from 'express';
import crypto from 'crypto';
import { logger } from '../utils/logger';

export interface DeviceAuthRequest extends Request {
  device?: {
    nodeId: string;
    apiKey: string;
  };
}

// In production, this would be stored in database
const deviceApiKeys = new Map<string, string>();

export function generateApiKey(): string {
  return `gg_${crypto.randomBytes(32).toString('hex')}`;
}

export function registerDeviceApiKey(nodeId: string, apiKey: string): void {
  deviceApiKeys.set(apiKey, nodeId);
}

export const apiKeyMiddleware = (
  req: DeviceAuthRequest,
  res: Response,
  next: NextFunction
): void => {
  try {
    const apiKey = req.headers['x-api-key'] as string;

    if (!apiKey) {
      res.status(401).json({ error: 'API key required' });
      return;
    }

    const nodeId = deviceApiKeys.get(apiKey);
    if (!nodeId) {
      res.status(401).json({ error: 'Invalid API key' });
      return;
    }

    req.device = { nodeId, apiKey };
    next();
  } catch (error) {
    logger.error('API key middleware error:', error);
    res.status(500).json({ error: 'Authentication error' });
  }
};

export const optionalApiKey = (
  req: DeviceAuthRequest,
  res: Response,
  next: NextFunction
): void => {
  const apiKey = req.headers['x-api-key'] as string;

  if (apiKey) {
    const nodeId = deviceApiKeys.get(apiKey);
    if (nodeId) {
      req.device = { nodeId, apiKey };
    }
  }

  next();
};
