/**
 * Retry Helper Utility
 * Grid-Guardian - Transaction Retry Logic
 */

const logger = require('./logger');

/**
 * Execute a function with exponential backoff retry
 */
async function withRetry(fn, options = {}) {
  const {
    maxAttempts = 3,
    baseDelayMs = 1000,
    maxDelayMs = 30000,
    exponential = true,
    onRetry = null,
    shouldRetry = () => true,
  } = options;

  let lastError;
  let delay = baseDelayMs;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn(attempt);
    } catch (error) {
      lastError = error;

      if (attempt >= maxAttempts || !shouldRetry(error, attempt)) {
        throw error;
      }

      if (onRetry) {
        onRetry(error, attempt, delay);
      }

      logger.warn(`Attempt ${attempt} failed, retrying in ${delay}ms:`, error.message);

      await sleep(delay);

      if (exponential) {
        delay = Math.min(delay * 2, maxDelayMs);
      }
    }
  }

  throw lastError;
}

/**
 * Execute with timeout
 */
async function withTimeout(fn, timeoutMs, errorMessage = 'Operation timed out') {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(errorMessage));
    }, timeoutMs);

    fn()
      .then((result) => {
        clearTimeout(timer);
        resolve(result);
      })
      .catch((error) => {
        clearTimeout(timer);
        reject(error);
      });
  });
}

/**
 * Sleep helper
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Create idempotency key
 */
function createIdempotencyKey(operation, ...params) {
  const { createHash } = require('crypto');
  const normalizedParams = params.map((param) => {
    if (param === undefined) return 'undefined';
    if (param === null) return 'null';
    if (typeof param === 'object') {
      try {
        return JSON.stringify(param);
      } catch {
        return String(param);
      }
    }
    return String(param);
  });

  const data = [operation, ...normalizedParams].join(':');
  return createHash('sha256').update(data).digest('hex').substring(0, 32);
}

/**
 * Circuit breaker implementation
 */
class CircuitBreaker {
  constructor(options = {}) {
    this.failureThreshold = options.failureThreshold || 5;
    this.resetTimeoutMs = options.resetTimeoutMs || 60000;
    this.halfOpenMaxCalls = options.halfOpenMaxCalls || 1;

    this.state = 'CLOSED'; // CLOSED, OPEN, HALF_OPEN
    this.failureCount = 0;
    this.successCount = 0;
    this.lastFailureTime = null;
    this.halfOpenCallCount = 0;
  }

  async execute(fn) {
    if (this.state === 'OPEN') {
      if (Date.now() - this.lastFailureTime >= this.resetTimeoutMs) {
        this.state = 'HALF_OPEN';
        this.halfOpenCallCount = 0;
      } else {
        throw new Error('Circuit breaker is OPEN');
      }
    }

    if (this.state === 'HALF_OPEN' && this.halfOpenCallCount >= this.halfOpenMaxCalls) {
      throw new Error('Circuit breaker is HALF_OPEN and max calls reached');
    }

    try {
      if (this.state === 'HALF_OPEN') {
        this.halfOpenCallCount++;
      }

      const result = await fn();

      this._onSuccess();
      return result;
    } catch (error) {
      this._onFailure();
      throw error;
    }
  }

  _onSuccess() {
    this.failureCount = 0;

    if (this.state === 'HALF_OPEN') {
      this.state = 'CLOSED';
      logger.info('Circuit breaker CLOSED');
    }
  }

  _onFailure() {
    this.failureCount++;
    this.lastFailureTime = Date.now();

    if (this.state === 'HALF_OPEN' || this.failureCount >= this.failureThreshold) {
      this.state = 'OPEN';
      logger.warn(`Circuit breaker OPEN after ${this.failureCount} failures`);
    }
  }

  getState() {
    return {
      state: this.state,
      failureCount: this.failureCount,
      lastFailureTime: this.lastFailureTime,
    };
  }

  reset() {
    this.state = 'CLOSED';
    this.failureCount = 0;
    this.successCount = 0;
    this.lastFailureTime = null;
    this.halfOpenCallCount = 0;
  }
}

/**
 * Deduplication helper for events
 */
class EventDeduplicator {
  constructor(ttlMs = 60000, maxSize = 10000) {
    this.seen = new Map();
    this.ttlMs = ttlMs;
    this.maxSize = maxSize;
  }

  /**
   * Check if event is duplicate and mark as seen
   */
  isDuplicate(eventId) {
    this._cleanup();

    if (this.seen.has(eventId)) {
      return true;
    }

    this.seen.set(eventId, Date.now());
    return false;
  }

  /**
   * Create event ID from event data
   */
  createEventId(event) {
    return `${event.transactionHash}:${event.logIndex || 0}`;
  }

  _cleanup() {
    const now = Date.now();

    // Remove expired entries
    for (const [key, timestamp] of this.seen) {
      if (now - timestamp > this.ttlMs) {
        this.seen.delete(key);
      }
    }

    // Trim if too large
    if (this.seen.size > this.maxSize) {
      const keysToDelete = Array.from(this.seen.keys()).slice(
        0,
        this.seen.size - this.maxSize
      );
      keysToDelete.forEach((key) => this.seen.delete(key));
    }
  }

  clear() {
    this.seen.clear();
  }

  getStats() {
    return {
      size: this.seen.size,
      maxSize: this.maxSize,
      ttlMs: this.ttlMs,
    };
  }
}

module.exports = {
  withRetry,
  withTimeout,
  sleep,
  createIdempotencyKey,
  CircuitBreaker,
  EventDeduplicator,
};
