/**
 * Device Service - Pi device management
 *
 * Handles device registration, status tracking, heartbeat monitoring,
 * and command dispatch for Raspberry Pi edge devices.
 */
import { Node, Telemetry, AuditLog } from '../models';
import { logger } from '../utils/logger';
import { Op } from 'sequelize';

export interface DeviceInfo {
  nodeId: string;
  ownerAddress: string;
  pubkeyHash: string;
  metaUri?: string;
  archetype: string;
  status: 'pending' | 'active' | 'attested' | 'revoked' | 'offline';
  registeredAt: Date;
  attestedAt?: Date;
  lastSeen?: Date;
  metadata?: Record<string, unknown>;
}

export interface DeviceCommand {
  nodeId: string;
  command: string;
  params?: Record<string, unknown>;
  timestamp: Date;
  status: 'pending' | 'sent' | 'acknowledged' | 'failed';
}

// In-memory command queue (use Redis in production)
const commandQueue: Map<string, DeviceCommand[]> = new Map();

// Device heartbeat tracking
const deviceHeartbeats: Map<string, number> = new Map();
const HEARTBEAT_TIMEOUT_MS = 60000; // 60 seconds

class DeviceService {
  private heartbeatChecker: NodeJS.Timeout | null = null;

  /**
   * Start the heartbeat monitoring loop
   */
  startHeartbeatMonitoring(): void {
    if (this.heartbeatChecker) return;

    this.heartbeatChecker = setInterval(async () => {
      await this.checkHeartbeats();
    }, 30000); // Check every 30 seconds

    logger.info('Device heartbeat monitoring started');
  }

  /**
   * Stop heartbeat monitoring
   */
  stopHeartbeatMonitoring(): void {
    if (this.heartbeatChecker) {
      clearInterval(this.heartbeatChecker);
      this.heartbeatChecker = null;
    }
  }

  /**
   * Check device heartbeats and mark offline if timeout exceeded
   */
  private async checkHeartbeats(): Promise<void> {
    const now = Date.now();
    const offlineNodes: string[] = [];

    deviceHeartbeats.forEach((lastSeen, nodeId) => {
      if (now - lastSeen > HEARTBEAT_TIMEOUT_MS) {
        offlineNodes.push(nodeId);
      }
    });

    for (const nodeId of offlineNodes) {
      try {
        await this.markDeviceOffline(nodeId);
        deviceHeartbeats.delete(nodeId);
      } catch (error) {
        logger.error(`Failed to mark device ${nodeId} offline:`, error);
      }
    }
  }

  /**
   * Register a new device in the database
   */
  async registerDevice(deviceInfo: Omit<DeviceInfo, 'registeredAt' | 'status'>): Promise<Node> {
    const existing = await Node.findOne({ where: { nodeId: deviceInfo.nodeId } });

    if (existing) {
      // Update existing record
      await existing.update({
        ownerAddress: deviceInfo.ownerAddress,
        pubkeyHash: deviceInfo.pubkeyHash,
        metaUri: deviceInfo.metaUri,
        archetype: deviceInfo.archetype || 'prosumer',
        metadata: deviceInfo.metadata,
        status: 'active',
      });
      logger.info(`Device updated: ${deviceInfo.nodeId}`);
      return existing;
    }

    const node = await Node.create({
      nodeId: deviceInfo.nodeId,
      ownerAddress: deviceInfo.ownerAddress,
      pubkeyHash: deviceInfo.pubkeyHash,
      metaUri: deviceInfo.metaUri || null,
      archetype: deviceInfo.archetype || 'prosumer',
      metadata: deviceInfo.metadata || null,
    });

    await this.logAudit('device_registered', 'node', deviceInfo.nodeId, 'register', {
      ownerAddress: deviceInfo.ownerAddress,
    });

    logger.info(`Device registered: ${deviceInfo.nodeId}`);
    return node;
  }

  /**
   * Get device by nodeId
   */
  async getDevice(nodeId: string): Promise<Node | null> {
    return Node.findOne({ where: { nodeId } });
  }

  /**
   * List all devices with pagination
   */
  async listDevices(options: {
    page?: number;
    limit?: number;
    status?: string;
    archetype?: string;
  } = {}): Promise<{ nodes: Node[]; total: number; pages: number }> {
    const page = options.page || 1;
    const limit = Math.min(options.limit || 20, 100);
    const offset = (page - 1) * limit;

    const where: Record<string, unknown> = {};
    if (options.status) where.status = options.status;
    if (options.archetype) where.archetype = options.archetype;

    const { rows, count } = await Node.findAndCountAll({
      where,
      limit,
      offset,
      order: [['registeredAt', 'DESC']],
    });

    return {
      nodes: rows,
      total: count,
      pages: Math.ceil(count / limit),
    };
  }

  /**
   * Update device heartbeat (called when telemetry received)
   */
  async updateHeartbeat(nodeId: string): Promise<void> {
    deviceHeartbeats.set(nodeId, Date.now());

    // Update lastSeen in database
    await Node.update(
      { lastSeen: new Date(), status: 'active' },
      { where: { nodeId, status: { [Op.ne]: 'revoked' } } }
    );
  }

  /**
   * Mark device as offline
   */
  async markDeviceOffline(nodeId: string): Promise<void> {
    await Node.update(
      { status: 'pending' }, // Use pending to indicate offline
      { where: { nodeId, status: { [Op.notIn]: ['revoked', 'pending'] } } }
    );

    await this.logAudit('device_offline', 'system', nodeId, 'status_change', {
      reason: 'heartbeat_timeout',
    });

    logger.warn(`Device marked offline: ${nodeId}`);
  }

  /**
   * Mark device as attested
   */
  async attestDevice(nodeId: string): Promise<void> {
    await Node.update(
      { status: 'attested', attestedAt: new Date() },
      { where: { nodeId } }
    );

    await this.logAudit('device_attested', 'system', nodeId, 'attest', {});
    logger.info(`Device attested: ${nodeId}`);
  }

  /**
   * Revoke device
   */
  async revokeDevice(nodeId: string, reason: string): Promise<void> {
    await Node.update(
      { status: 'revoked' },
      { where: { nodeId } }
    );

    await this.logAudit('device_revoked', 'system', nodeId, 'revoke', { reason });
    logger.warn(`Device revoked: ${nodeId}, reason: ${reason}`);
  }

  /**
   * Queue a command for a device
   */
  queueCommand(command: Omit<DeviceCommand, 'status'>): void {
    const cmd: DeviceCommand = {
      ...command,
      status: 'pending',
    };

    const queue = commandQueue.get(command.nodeId) || [];
    queue.push(cmd);
    commandQueue.set(command.nodeId, queue);

    logger.info(`Command queued for ${command.nodeId}: ${command.command}`);
  }

  /**
   * Get pending commands for a device
   */
  getPendingCommands(nodeId: string): DeviceCommand[] {
    return commandQueue.get(nodeId)?.filter(cmd => cmd.status === 'pending') || [];
  }

  /**
   * Mark command as sent
   */
  markCommandSent(nodeId: string, command: string): void {
    const queue = commandQueue.get(nodeId);
    if (!queue) return;

    const cmd = queue.find(c => c.command === command && c.status === 'pending');
    if (cmd) {
      cmd.status = 'sent';
    }
  }

  /**
   * Clear old commands from queue
   */
  clearOldCommands(maxAgeMs: number = 300000): void {
    const now = Date.now();

    commandQueue.forEach((queue, nodeId) => {
      const filtered = queue.filter(
        cmd => now - cmd.timestamp.getTime() < maxAgeMs || cmd.status === 'pending'
      );
      commandQueue.set(nodeId, filtered);
    });
  }

  /**
   * Get device statistics
   */
  async getDeviceStats(): Promise<{
    total: number;
    active: number;
    attested: number;
    offline: number;
    revoked: number;
  }> {
    const stats = await Node.findAll({
      attributes: [
        'status',
        [Node.sequelize!.fn('COUNT', Node.sequelize!.col('status')), 'count'],
      ],
      group: ['status'],
      raw: true,
    }) as unknown as { status: string; count: string }[];

    const result = {
      total: 0,
      active: 0,
      attested: 0,
      offline: 0,
      revoked: 0,
    };

    stats.forEach(({ status, count }) => {
      const num = parseInt(count);
      result.total += num;
      if (status === 'active') result.active = num;
      else if (status === 'attested') result.attested = num;
      else if (status === 'pending') result.offline = num;
      else if (status === 'revoked') result.revoked = num;
    });

    return result;
  }

  /**
   * Log an audit event
   */
  private async logAudit(
    eventType: string,
    actorType: 'user' | 'node' | 'system',
    resourceId: string,
    action: string,
    details: Record<string, unknown>
  ): Promise<void> {
    try {
      await AuditLog.create({
        eventType,
        actorType,
        actorId: actorType === 'system' ? 'system' : resourceId,
        resourceType: 'node',
        resourceId,
        action,
        details,
      });
    } catch (error) {
      logger.error('Failed to create audit log:', error);
    }
  }
}

export const deviceService = new DeviceService();
