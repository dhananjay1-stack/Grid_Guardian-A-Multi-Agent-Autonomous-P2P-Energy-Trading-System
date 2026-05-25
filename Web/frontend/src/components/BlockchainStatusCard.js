import React from 'react';

function BlockchainStatusCard({ status }) {
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Blockchain Status</span>
        <span className={`card-badge ${status.connected ? 'badge-online' : 'badge-offline'}`}>
          {status.connected ? 'CONNECTED' : 'DISCONNECTED'}
        </span>
      </div>

      <div className="blockchain-status">
        <div className="blockchain-item">
          <span className="blockchain-label">Block Number</span>
          <span className="blockchain-value">
            #{status.block_number || 0}
          </span>
        </div>
        <div className="blockchain-item">
          <span className="blockchain-label">Pending Trades</span>
          <span className="blockchain-value">
            {status.pending_trades || 0}
          </span>
        </div>
        <div className="blockchain-item">
          <span className="blockchain-label">Settlements Today</span>
          <span className="blockchain-value">
            {status.settlements_today || 0}
          </span>
        </div>
        <div className="blockchain-item">
          <span className="blockchain-label">Network</span>
          <span className="blockchain-value" style={{ fontSize: '12px' }}>
            {status.network || 'Hardhat Local'}
          </span>
        </div>
      </div>
    </div>
  );
}

export default BlockchainStatusCard;
