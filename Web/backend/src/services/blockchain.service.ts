/**
 * Blockchain Service - Ethers.js contract interactions
 */
import { ethers, JsonRpcProvider, Wallet, Contract } from 'ethers';
import { config } from '../config/env';
import { logger } from '../utils/logger';

// Contract ABIs (minimal, add more functions as needed)
const IDENTITY_ABI = [
  'function registerNodeMeta(bytes32 nodeId, bytes32 pubkeyHash, string metaURI, uint256 nonce, uint256 expiry, bytes signature) external',
  'function registerNode(bytes32 nodeId, bytes32 pubkeyHash, string metaURI) external',
  'function attestNode(bytes32 nodeId) external',
  'function nonces(address) view returns (uint256)',
  'function nodes(bytes32) view returns (address owner, bytes32 pubkeyHash, string metaURI, uint256 stake, uint256 registeredAt, bool active, bool attested)',
  'event NodeRegistered(bytes32 indexed nodeId, address indexed owner, string metaURI, uint256 timestamp)',
];

const COLLATERAL_ABI = [
  'function deposit(bytes32 nodeId) payable external',
  'function withdraw(bytes32 nodeId, uint256 amount) external',
  'function deposits(bytes32) view returns (uint256)',
  'function lockFunds(bytes32 nodeId, uint256 amount, bytes32 tradeId) external',
  'function unlockFunds(bytes32 tradeId) external',
  'function transferLockedFunds(bytes32 tradeId, address to) external',
  'function relayerAllowance(bytes32, address) view returns (uint256)',
  'function voucherNonce(bytes32) view returns (uint256)',
];

const SETTLEMENT_ABI = [
  'function proposeTrade(bytes32 tradeId, bytes32 matchHash, bytes32 buyerNodeId, bytes32 sellerNodeId, uint16 kwhBucket, uint16 priceBucket) external',
  'function markDelivered(bytes32 tradeId) external',
  'function executeSettlement(bytes32 tradeId) external',
  'function refundTrade(bytes32 tradeId) external',
  'function trades(bytes32) view returns (bytes32 matchHash, bytes32 buyerNodeId, bytes32 sellerNodeId, uint16 kwhBucket, uint16 priceBucket, uint256 lockedAmount, uint8 status, uint256 proposedBlock, uint256 deliveredBlock)',
  'event TradeProposed(bytes32 indexed tradeId, bytes32 indexed matchHash, bytes32 buyerNodeId, bytes32 sellerNodeId, uint16 kwhBucket, uint16 priceBucket, uint256 lockedAmount)',
  'event TradeExecuted(bytes32 indexed tradeId, bytes32 indexed buyerNodeId, bytes32 indexed sellerNodeId, uint16 kwhBucket, uint16 priceBucket)',
  'event DeliveryMarked(bytes32 indexed tradeId, uint256 deliveredBlock)',
  'event SettlementCompleted(bytes32 indexed tradeId, uint256 amount)',
];

const DELIVERY_ABI = [
  'function submitReceipt(bytes32 tradeId, bytes32 nodeId, bytes32 meterSnapshotHash, uint16 deliveredKwhBucket, uint256 periodStart, uint256 periodEnd, uint256 nonce, bytes signature) external',
  'function getDeliveryNonce(bytes32 nodeId) view returns (uint256)',
  'function getReceiptCount(bytes32 tradeId) view returns (uint256)',
  'event DeliveryReceiptSubmitted(bytes32 indexed tradeId, bytes32 indexed nodeId, bytes32 meterSnapshotHash)',
];

class BlockchainService {
  private provider: JsonRpcProvider;
  private wallet: Wallet;
  private identityContract: Contract | null = null;
  private collateralContract: Contract | null = null;
  private settlementContract: Contract | null = null;
  private deliveryContract: Contract | null = null;

  constructor() {
    this.provider = new JsonRpcProvider(config.rpcUrl);
    this.wallet = new Wallet(config.relayerPrivateKey, this.provider);
  }

  async initialize(): Promise<void> {
    try {
      const network = await this.provider.getNetwork();
      logger.info(`Connected to chain ID: ${network.chainId}`);
      this.initContracts();
    } catch (error) {
      logger.error('Failed to initialize blockchain service:', error);
      throw error;
    }
  }

  private initContracts(): void {
    if (config.contracts.identity) {
      this.identityContract = new Contract(config.contracts.identity, IDENTITY_ABI, this.wallet);
    }
    if (config.contracts.collateral) {
      this.collateralContract = new Contract(config.contracts.collateral, COLLATERAL_ABI, this.wallet);
    }
    if (config.contracts.settlement) {
      this.settlementContract = new Contract(config.contracts.settlement, SETTLEMENT_ABI, this.wallet);
    }
    if (config.contracts.deliveryRegistry) {
      this.deliveryContract = new Contract(config.contracts.deliveryRegistry, DELIVERY_ABI, this.wallet);
    }
  }

  // ═══════════════════════════════════════════════════════════════════
  //                        STATUS & INFO
  // ═══════════════════════════════════════════════════════════════════

  async getStatus(): Promise<{
    connected: boolean;
    chainId: number;
    blockNumber: number;
    relayerAddress: string;
    relayerBalance: string;
  }> {
    try {
      const [network, blockNumber, balance] = await Promise.all([
        this.provider.getNetwork(),
        this.provider.getBlockNumber(),
        this.provider.getBalance(this.wallet.address),
      ]);

      return {
        connected: true,
        chainId: Number(network.chainId),
        blockNumber,
        relayerAddress: this.wallet.address,
        relayerBalance: ethers.formatEther(balance),
      };
    } catch (error) {
      return {
        connected: false,
        chainId: 0,
        blockNumber: 0,
        relayerAddress: this.wallet.address,
        relayerBalance: '0',
      };
    }
  }

  getContractAddresses(): Record<string, string> {
    return {
      identity: config.contracts.identity,
      collateral: config.contracts.collateral,
      trading: config.contracts.trading,
      matchRegistry: config.contracts.matchRegistry,
      settlement: config.contracts.settlement,
      deliveryRegistry: config.contracts.deliveryRegistry,
    };
  }

  // ═══════════════════════════════════════════════════════════════════
  //                        IDENTITY
  // ═══════════════════════════════════════════════════════════════════

  async getNonce(address: string): Promise<number> {
    if (!this.identityContract) throw new Error('Identity contract not configured');
    const nonce = await this.identityContract.nonces(address);
    return Number(nonce);
  }

  async getNode(nodeId: string): Promise<{
    owner: string;
    pubkeyHash: string;
    metaURI: string;
    stake: string;
    registeredAt: number;
    active: boolean;
    attested: boolean;
  } | null> {
    if (!this.identityContract) throw new Error('Identity contract not configured');
    try {
      const node = await this.identityContract.nodes(nodeId);
      return {
        owner: node.owner,
        pubkeyHash: node.pubkeyHash,
        metaURI: node.metaURI,
        stake: ethers.formatEther(node.stake),
        registeredAt: Number(node.registeredAt),
        active: node.active,
        attested: node.attested,
      };
    } catch {
      return null;
    }
  }

  async registerNodeMeta(
    nodeId: string,
    pubkeyHash: string,
    metaURI: string,
    nonce: number,
    expiry: number,
    signature: string
  ): Promise<string> {
    if (!this.identityContract) throw new Error('Identity contract not configured');
    const tx = await this.identityContract.registerNodeMeta(
      nodeId, pubkeyHash, metaURI, nonce, expiry, signature
    );
    const receipt = await tx.wait();
    logger.info(`Node registered: ${nodeId}, tx: ${receipt.hash}`);
    return receipt.hash;
  }

  async attestNode(nodeId: string): Promise<string> {
    if (!this.identityContract) throw new Error('Identity contract not configured');
    const tx = await this.identityContract.attestNode(nodeId);
    const receipt = await tx.wait();
    logger.info(`Node attested: ${nodeId}, tx: ${receipt.hash}`);
    return receipt.hash;
  }

  // ═══════════════════════════════════════════════════════════════════
  //                        COLLATERAL
  // ═══════════════════════════════════════════════════════════════════

  async getDeposit(nodeId: string): Promise<string> {
    if (!this.collateralContract) throw new Error('Collateral contract not configured');
    const deposit = await this.collateralContract.deposits(nodeId);
    return ethers.formatEther(deposit);
  }

  async getRelayerAllowance(nodeId: string, relayer: string): Promise<string> {
    if (!this.collateralContract) throw new Error('Collateral contract not configured');
    const allowance = await this.collateralContract.relayerAllowance(nodeId, relayer);
    return ethers.formatEther(allowance);
  }

  async getVoucherNonce(nodeId: string): Promise<number> {
    if (!this.collateralContract) throw new Error('Collateral contract not configured');
    const nonce = await this.collateralContract.voucherNonce(nodeId);
    return Number(nonce);
  }

  // ═══════════════════════════════════════════════════════════════════
  //                        SETTLEMENT
  // ═══════════════════════════════════════════════════════════════════

  async getTrade(tradeId: string): Promise<{
    matchHash: string;
    buyerNodeId: string;
    sellerNodeId: string;
    kwhBucket: number;
    priceBucket: number;
    lockedAmount: string;
    status: number;
    proposedBlock: number;
    deliveredBlock: number;
  } | null> {
    if (!this.settlementContract) throw new Error('Settlement contract not configured');
    try {
      const trade = await this.settlementContract.trades(tradeId);
      return {
        matchHash: trade.matchHash,
        buyerNodeId: trade.buyerNodeId,
        sellerNodeId: trade.sellerNodeId,
        kwhBucket: Number(trade.kwhBucket),
        priceBucket: Number(trade.priceBucket),
        lockedAmount: ethers.formatEther(trade.lockedAmount),
        status: Number(trade.status),
        proposedBlock: Number(trade.proposedBlock),
        deliveredBlock: Number(trade.deliveredBlock),
      };
    } catch {
      return null;
    }
  }

  // ═══════════════════════════════════════════════════════════════════
  //                        DELIVERY
  // ═══════════════════════════════════════════════════════════════════

  async getDeliveryNonce(nodeId: string): Promise<number> {
    if (!this.deliveryContract) throw new Error('Delivery contract not configured');
    const nonce = await this.deliveryContract.getDeliveryNonce(nodeId);
    return Number(nonce);
  }

  async getReceiptCount(tradeId: string): Promise<number> {
    if (!this.deliveryContract) throw new Error('Delivery contract not configured');
    const count = await this.deliveryContract.getReceiptCount(tradeId);
    return Number(count);
  }

  async submitDeliveryReceipt(
    tradeId: string,
    nodeId: string,
    meterSnapshotHash: string,
    deliveredKwhBucket: number,
    periodStart: number,
    periodEnd: number,
    nonce: number,
    signature: string
  ): Promise<string> {
    if (!this.deliveryContract) throw new Error('Delivery contract not configured');
    const tx = await this.deliveryContract.submitReceipt(
      tradeId, nodeId, meterSnapshotHash, deliveredKwhBucket, periodStart, periodEnd, nonce, signature
    );
    const receipt = await tx.wait();
    logger.info(`Delivery receipt submitted for trade ${tradeId}, tx: ${receipt.hash}`);
    return receipt.hash;
  }

  // ═══════════════════════════════════════════════════════════════════
  //                        EVENT LISTENERS
  // ═══════════════════════════════════════════════════════════════════

  onTradeExecuted(callback: (tradeId: string, buyerNodeId: string, sellerNodeId: string, kwhBucket: number, priceBucket: number) => void): void {
    if (!this.settlementContract) return;
    this.settlementContract.on('TradeExecuted', callback);
  }

  onSettlementCompleted(callback: (tradeId: string, amount: bigint) => void): void {
    if (!this.settlementContract) return;
    this.settlementContract.on('SettlementCompleted', callback);
  }

  onDeliveryReceiptSubmitted(callback: (tradeId: string, nodeId: string, meterSnapshotHash: string) => void): void {
    if (!this.deliveryContract) return;
    this.deliveryContract.on('DeliveryReceiptSubmitted', callback);
  }

  removeAllListeners(): void {
    this.settlementContract?.removeAllListeners();
    this.deliveryContract?.removeAllListeners();
  }
}

export const blockchainService = new BlockchainService();
