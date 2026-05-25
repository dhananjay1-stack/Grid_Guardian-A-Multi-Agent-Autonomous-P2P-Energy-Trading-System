import React from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';

function TelemetryChart({ data }) {
  return (
    <div className="card" style={{ gridColumn: 'span 2' }}>
      <div className="card-header">
        <span className="card-title">Real-time Telemetry</span>
        <span style={{ fontSize: '12px', color: '#888' }}>
          Last 30 readings
        </span>
      </div>

      <div className="chart-container">
        {data.length === 0 ? (
          <div className="empty-state">
            Waiting for telemetry data...
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis
                dataKey="time"
                tick={{ fill: '#888', fontSize: 10 }}
                axisLine={{ stroke: '#333' }}
              />
              <YAxis
                yAxisId="power"
                tick={{ fill: '#888', fontSize: 10 }}
                axisLine={{ stroke: '#333' }}
                label={{ value: 'W', angle: -90, position: 'insideLeft', fill: '#888' }}
              />
              <YAxis
                yAxisId="soc"
                orientation="right"
                tick={{ fill: '#888', fontSize: 10 }}
                axisLine={{ stroke: '#333' }}
                domain={[0, 100]}
                label={{ value: '%', angle: 90, position: 'insideRight', fill: '#888' }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1a1a2e',
                  border: '1px solid #333',
                  borderRadius: '8px',
                }}
                labelStyle={{ color: '#888' }}
              />
              <Legend wrapperStyle={{ color: '#888' }} />
              <Line
                yAxisId="power"
                type="monotone"
                dataKey="power"
                stroke="#00d9ff"
                strokeWidth={2}
                dot={false}
                name="Power (W)"
              />
              <Line
                yAxisId="soc"
                type="monotone"
                dataKey="soc"
                stroke="#00ff88"
                strokeWidth={2}
                dot={false}
                name="Battery SoC (%)"
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

export default TelemetryChart;
