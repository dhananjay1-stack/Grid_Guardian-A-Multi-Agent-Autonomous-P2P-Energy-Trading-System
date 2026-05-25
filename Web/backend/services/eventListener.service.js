/**
 * Event Listener Service
 * Grid-Guardian - Blockchain Event Monitoring
 */

const EventEmitter = require('events');
const web3Client = require('../utils/web3Client');
const blockchainConfig = require('../config/blockchain.config');
const { EventDeduplicator, withRetry } = require('../utils/retryHelper');
const BlockchainLog = require('../models/blockchainLog.model');
const logger = require('../utils/logger');

class EventListenerService extends EventEmitter {
  constructor() {
    super();
    this.isRunning = false;
    this.lastProcessedBlock = new Map(); // contract -> blockNumber
    this.deduplicator = new EventDeduplicator(300000, 50000); // 5-min TTL, 50k max
    this.pollInterval = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 10;
  }

  /**
   * Start listening to blockchain events
   */
  async start() {
    if (this.isRunning) {
      logger.warn('Event listener is already running');
      return;
    }

    try {
      // Initialize starting block for each contract
      const currentBlock = await web3Client.getBlockNumber();
      const startBlock = Math.max(0, currentBlock - 100); // Start from ~100 blocks ago, bounded at 0

      for (const contractName of Object.keys(blockchainConfig.contracts)) {
        if (web3Client.hasContract(contractName)) {
          this.lastProcessedBlock.set(contractName, startBlock);
        }
      }

      this.isRunning = true;
      this.reconnectAttempts = 0;

      // Start polling for events
      this._startPolling();

      // Also set up websocket subscriptions if provider supports it
      this._setupEventSubscriptions();

      logger.info(`Event listener started at block ${currentBlock}`);
    } catch (error) {
      logger.error('Failed to start event listener:', error);
      this._scheduleReconnect();
    }
  }

  /**
   * Stop listening to events
   */
  stop() {
    this.isRunning = false;

    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }

    // Unsubscribe from all contracts
    for (const contractName of Object.keys(blockchainConfig.contracts)) {
      if (web3Client.hasContract(contractName)) {
        web3Client.unsubscribeAll(web3Client.getContract(contractName));
      }
    }

    logger.info('Event listener stopped');
  }

  /**
   * Start polling for events
   */
  _startPolling() {
    const pollIntervalMs = blockchainConfig.eventListener.pollIntervalMs;

    this.pollInterval = setInterval(async () => {
      if (!this.isRunning) return;

      try {
        await this._pollEvents();
      } catch (error) {
        logger.error('Error polling events:', error);
        this._handlePollError(error);
      }
    }, pollIntervalMs);
  }

  /**
   * Poll for past events from all contracts
   */
  async _pollEvents() {
    const currentBlock = await web3Client.getBlockNumber();
    const maxBlocksPerPoll = blockchainConfig.eventListener.maxBlocksPerPoll;

    // Poll each contract
    const contractsToPoll = [
      { name: 'settlement', events: ['TradeProposed', 'TradeExecuted', 'DeliveryMarked', 'SettlementCompleted', 'TradeRefunded', 'TradeDisputed', 'DisputeResolved'] },
      { name: 'trading', events: ['CommitPosted', 'OfferRevealed'] },
      { name: 'matchRegistry', events: ['MatchPublished', 'MatchChallenged', 'MatchFinalized', 'MatchInvalidated'] },
      { name: 'deliveryRegistry', events: ['DeliveryReceiptSubmitted', 'DeliveryConfirmed'] },
      { name: 'collateral', events: ['Deposited', 'FundsLockedForTrade', 'FundsUnlockedForTrade', 'FundsTransferredForTrade'] },
      { name: 'identity', events: ['NodeRegistered', 'NodeRevoked', 'NodeAttested'] },
    ];

    for (const { name, events } of contractsToPoll) {
      if (!web3Client.hasContract(name)) continue;

      const contract = web3Client.getContract(name);
      const fromBlock = (this.lastProcessedBlock.get(name) || Math.max(0, currentBlock - 100)) + 1;
      const toBlock = Math.min(fromBlock + maxBlocksPerPoll - 1, currentBlock);

      if (fromBlock > currentBlock) continue;

      for (const eventName of events) {
        try {
          const events = await web3Client.getPastEvents(contract, eventName, fromBlock, toBlock);

          for (const event of events) {
            await this._processEvent(name, event);
          }
        } catch (error) {
          if (!error.message.includes('no matching event')) {
            logger.error(`Error fetching ${eventName} from ${name}:`, error.message);
          }
        }
      }

      this.lastProcessedBlock.set(name, toBlock);
    }
  }

  /**
   * Set up real-time event subscriptions
   */
  _setupEventSubscriptions() {
    // Settlement contract events (most important)
    if (web3Client.hasContract('settlement')) {
      const settlement = web3Client.getContract('settlement');

      this._subscribeWithHandler('settlement', settlement, 'TradeExecuted', (event) => {
        this.emit('trade:executed', this._formatTradeExecutedEvent(event));
      });

      this._subscribeWithHandler('settlement', settlement, 'SettlementCompleted', (event) => {
        this.emit('settlement:completed', this._formatSettlementEvent(event));
      });

      this._subscribeWithHandler('settlement', settlement, 'DeliveryMarked', (event) => {
        this.emit('delivery:marked', this._formatDeliveryEvent(event));
      });
    }

    // Delivery Registry events
    if (web3Client.hasContract('deliveryRegistry')) {
      const delivery = web3Client.getContract('deliveryRegistry');

      this._subscribeWithHandler('deliveryRegistry', delivery, 'DeliveryReceiptSubmitted', (event) => {
        this.emit('receipt:submitted', this._formatReceiptEvent(event));
      });

      this._subscribeWithHandler('deliveryRegistry', delivery, 'DeliveryConfirmed', (event) => {
        this.emit('delivery:confirmed', event);
      });
    }

    // Match Registry events
    if (web3Client.hasContract('matchRegistry')) {
      const matchRegistry = web3Client.getContract('matchRegistry');

      this._subscribeWithHandler('matchRegistry', matchRegistry, 'MatchPublished', (event) => {
        this.emit('match:published', event);
      });

      this._subscribeWithHandler('matchRegistry', matchRegistry, 'MatchFinalized', (event) => {
        this.emit('match:finalized', event);
      });
    }
  }

  /**
   * Subscribe to an event with a handler
   */
  _subscribeWithHandler(contractName, contract, eventName, handler) {
    try {
      web3Client.subscribeToEvent(contract, eventName, async (event) => {
        try {
          await this._processEvent(contractName, event);
          handler(event);
        } catch (error) {
          logger.error(`Error processing ${eventName} event:`, error);
        }
      });
    } catch (error) {
      logger.error(`Failed to subscribe to ${eventName}:`, error);
    }
  }

  /**
   * Process and deduplicate an event
   */
  async _processEvent(contractName, event) {
    const eventId = this.deduplicator.createEventId(event);

    // Check for duplicate
    if (this.deduplicator.isDuplicate(eventId)) {
      logger.debug(`Duplicate event skipped: ${event.eventName} ${eventId}`);
      return false;
    }

    // Log to database
    try {
      const nodeId = this._extractNodeId(event);
      await BlockchainLog.create({
        node_id: nodeId || 'SYSTEM',
        event_type: event.eventName,
        tx_hash: event.transactionHash,
        block_number: event.blockNumber,
        log_index: event.logIndex,
        payload: {
          contract: contractName,
          args: this._formatEventArgs(event.args),
        },
        status: 'CONFIRMED',
      });
    } catch (error) {
      logger.error('Error saving event to DB:', error);
    }

    // Emit generic event
    this.emit('event', {
      contract: contractName,
      ...event,
    });

    return true;
  }

  /**
   * Extract node ID from event args
   */
  _extractNodeId(event) {
    const args = event.args;
    if (!args) return null;

    // Check different possible field names
    const nodeIdFields = ['nodeId', 'buyerNodeId', 'sellerNodeId', 'node_id'];
    for (const field of nodeIdFields) {
      if (args[field]) {
        return args[field];
      }
    }

    return null;
  }

  /**
   * Format event args for storage
   */
  _formatEventArgs(args) {
    if (!args) return {};

    const formatted = {};
    for (let i = 0; i < args.length; i++) {
      const key = args[i] ? Object.keys(args)[i] : i.toString();
      const value = args[i];

      if (typeof value === 'bigint') {
        formatted[key] = value.toString();
      } else if (typeof value === 'object' && value !== null) {
        formatted[key] = this._formatEventArgs(value);
      } else {
        formatted[key] = value;
      }
    }

    return formatted;
  }

  /**
   * Format TradeExecuted event
   */
  _formatTradeExecutedEvent(event) {
    return {
      tradeId: event.args[0],
      buyerNodeId: event.args[1],
      sellerNodeId: event.args[2],
      kwhBucket: Number(event.args[3]),
      priceBucket: Number(event.args[4]),
      transactionHash: event.transactionHash,
      blockNumber: event.blockNumber,
      timestamp: Date.now(),
    };
  }

  /**
   * Format Settlement event
   */
  _formatSettlementEvent(event) {
    return {
      tradeId: event.args[0],
      amount: event.args[1]?.toString(),
      transactionHash: event.transactionHash,
      blockNumber: event.blockNumber,
      timestamp: Date.now(),
    };
  }

  /**
   * Format Delivery event
   */
  _formatDeliveryEvent(event) {
    return {
      tradeId: event.args[0],
      deliveredBlock: Number(event.args[1]),
      transactionHash: event.transactionHash,
      blockNumber: event.blockNumber,
      timestamp: Date.now(),
    };
  }

  /**
   * Format Receipt event
   */
  _formatReceiptEvent(event) {
    return {
      tradeId: event.args[0],
      nodeId: event.args[1],
      meterSnapshotHash: event.args[2],
      deliveredKwhBucket: Number(event.args[3]),
      submittedBlock: Number(event.args[4]),
      transactionHash: event.transactionHash,
      blockNumber: event.blockNumber,
      timestamp: Date.now(),
    };
  }

  /**
   * Handle poll error
   */
  _handlePollError(error) {
    this.reconnectAttempts++;

    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      logger.error('Max reconnect attempts reached, stopping event listener');
      this.stop();
      this.emit('error', new Error('Max reconnect attempts reached'));
    } else {
      this._scheduleReconnect();
    }
  }

  /**
   * Schedule reconnection
   */
  _scheduleReconnect() {
    const wasRunning = this.isRunning;
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
    this.isRunning = false;

    const delay = blockchainConfig.eventListener.reconnectDelayMs * Math.min(this.reconnectAttempts, 5);

    logger.info(`Scheduling reconnect in ${delay}ms (attempt ${this.reconnectAttempts})`);

    setTimeout(async () => {
      if (wasRunning) {
        await this.start();
      }
    }, delay);
  }

  /**
   * Get listener status
   */
  getStatus() {
    const blocks = {};
    for (const [contract, block] of this.lastProcessedBlock) {
      blocks[contract] = block;
    }

    return {
      running: this.isRunning,
      reconnectAttempts: this.reconnectAttempts,
      lastProcessedBlocks: blocks,
      deduplicatorStats: this.deduplicator.getStats(),
    };
  }
}

// Export singleton
const eventListenerService = new EventListenerService();
module.exports = eventListenerService;
