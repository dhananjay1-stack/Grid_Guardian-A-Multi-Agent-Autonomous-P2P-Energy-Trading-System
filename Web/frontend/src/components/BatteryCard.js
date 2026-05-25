import React from 'react';

function BatteryCard({ battery }) {
  const socPercent = battery.soc || 50;
  const isLow = socPercent < 20;

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Battery Status</span>
        <span className={`card-badge ${battery.is_charging ? 'badge-online' : battery.is_discharging ? 'badge-offline' : ''}`}>
          {battery.is_charging ? 'CHARGING' : battery.is_discharging ? 'DISCHARGING' : 'IDLE'}
        </span>
      </div>

      <div className="battery-container">
        <div className="battery-visual">
          <div className="battery-terminal"></div>
          <div
            className={`battery-fill ${isLow ? 'low' : ''}`}
            style={{ height: `${socPercent}%` }}
          ></div>
          <div className="battery-percent">{socPercent.toFixed(0)}%</div>
        </div>

        <div className="battery-stats">
          <div className="battery-stat">
            <span style={{ color: '#888' }}>State of Charge</span>
            <span style={{ fontWeight: 600 }}>{battery.soc_kwh?.toFixed(2) || '0.00'} kWh</span>
          </div>
          <div className="battery-stat">
            <span style={{ color: '#888' }}>Capacity</span>
            <span style={{ fontWeight: 600 }}>{battery.capacity_kwh?.toFixed(2) || '4.00'} kWh</span>
          </div>
          <div className="battery-stat">
            <span style={{ color: '#888' }}>Power Flow</span>
            <span style={{ fontWeight: 600, color: battery.power_kw > 0 ? '#00ff88' : battery.power_kw < 0 ? '#ff4444' : '#888' }}>
              {battery.power_kw > 0 ? '+' : ''}{battery.power_kw?.toFixed(2) || '0.00'} kW
            </span>
          </div>
          <div className="battery-stat">
            <span style={{ color: '#888' }}>Available</span>
            <span style={{ fontWeight: 600 }}>
              {((battery.soc_kwh || 0) - (battery.capacity_kwh || 4) * 0.1).toFixed(2)} kWh
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default BatteryCard;
