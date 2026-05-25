import React from 'react';

function TradeListCard({ trades }) {
  const formatTime = (timestamp) => {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return date.toLocaleTimeString();
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'executed':
      case 'settled':
        return '#00ff88';
      case 'failed':
      case 'cancelled':
        return '#ff4444';
      case 'submitted':
        return '#00d9ff';
      default:
        return '#ffc107';
    }
  };

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Trade Proposals</span>
        <span style={{ fontSize: '12px', color: '#888' }}>
          {trades.length} trades
        </span>
      </div>

      <div className="trade-list">
        {trades.length === 0 ? (
          <div className="empty-state">
            No trade proposals yet
          </div>
        ) : (
          trades.map((trade, index) => (
            <div key={trade.trade_id || index} className="trade-item">
              <div>
                <span className={`trade-type ${trade.trade_type}`}>
                  {trade.trade_type}
                </span>
                <div className="trade-quantity" style={{ marginTop: '4px' }}>
                  {trade.quantity_kwh?.toFixed(3)} kWh
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="trade-status" style={{ color: getStatusColor(trade.status) }}>
                  {trade.status?.toUpperCase()}
                </div>
                <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
                  {formatTime(trade.timestamp)}
                </div>
                <div style={{ fontSize: '11px', color: '#888' }}>
                  {(trade.confidence * 100).toFixed(0)}% conf
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default TradeListCard;
