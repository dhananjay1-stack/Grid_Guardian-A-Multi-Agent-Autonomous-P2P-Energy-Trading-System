import React from 'react';

function NodeStatusCard({ status }) {
  const formatUptime = (seconds) => {
    if (!seconds) return '0s';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  };

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Node Status</span>
        <span className={`card-badge ${status.status === 'online' ? 'badge-online' : 'badge-offline'}`}>
          {status.status.toUpperCase()}
        </span>
      </div>

      <div className="node-status">
        <div className="stat-item">
          <div className="stat-value">{status.voltage?.toFixed(1) || '0'}V</div>
          <div className="stat-label">Voltage</div>
        </div>
        <div className="stat-item">
          <div className="stat-value">{status.current?.toFixed(2) || '0'}A</div>
          <div className="stat-label">Current</div>
        </div>
        <div className="stat-item">
          <div className="stat-value">{status.power?.toFixed(0) || '0'}W</div>
          <div className="stat-label">Power</div>
        </div>
        <div className="stat-item">
          <div className="stat-value">{formatUptime(status.uptime)}</div>
          <div className="stat-label">Uptime</div>
        </div>
      </div>

      <div style={{ marginTop: '16px', textAlign: 'center' }}>
        <span className={`condition-badge condition-${status.condition || 'normal'}`}>
          {(status.condition || 'normal').replace('_', ' ')}
        </span>
      </div>
    </div>
  );
}

export default NodeStatusCard;
