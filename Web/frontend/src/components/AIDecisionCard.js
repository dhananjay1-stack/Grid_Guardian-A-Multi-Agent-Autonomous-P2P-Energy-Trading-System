import React from 'react';

function AIDecisionCard({ decision }) {
  const getDecisionClass = () => {
    if (decision.trade_action) return `decision-${decision.trade_action}`;
    return `decision-${decision.decision}`;
  };

  const displayDecision = decision.trade_action || decision.decision;

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">AI Decision</span>
        {decision.is_mock && (
          <span className="card-badge badge-offline">MOCK</span>
        )}
      </div>

      <div className="ai-decision">
        <div className={`decision-badge ${getDecisionClass()}`}>
          {displayDecision}
        </div>

        <div style={{ fontSize: '14px', color: '#888', marginBottom: '8px' }}>
          {decision.action_name} ({decision.action_kw > 0 ? '+' : ''}{decision.action_kw} kW)
        </div>

        <div style={{ marginBottom: '8px' }}>
          <span className={`condition-badge condition-${decision.condition || 'normal'}`}>
            {(decision.condition || 'normal').replace('_', ' ')}
          </span>
        </div>

        <div style={{ textAlign: 'left', padding: '0 20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
            <span style={{ color: '#888' }}>Confidence</span>
            <span style={{ fontWeight: 600 }}>{(decision.confidence * 100).toFixed(1)}%</span>
          </div>
          <div className="confidence-bar">
            <div
              className="confidence-fill"
              style={{ width: `${decision.confidence * 100}%` }}
            />
          </div>
        </div>

        <div className="decision-details">
          <div className="detail-item">
            <div className="detail-label">Model</div>
            <div className="detail-value">{decision.model_key || 'N/A'}</div>
          </div>
          <div className="detail-item">
            <div className="detail-label">Power</div>
            <div className="detail-value">{decision.action_kw} kW</div>
          </div>
          {decision.recommended_quantity > 0 && (
            <>
              <div className="detail-item">
                <div className="detail-label">Trade Qty</div>
                <div className="detail-value">{decision.recommended_quantity?.toFixed(2)} kWh</div>
              </div>
              <div className="detail-item">
                <div className="detail-label">Trade Type</div>
                <div className="detail-value">{decision.trade_action}</div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default AIDecisionCard;
