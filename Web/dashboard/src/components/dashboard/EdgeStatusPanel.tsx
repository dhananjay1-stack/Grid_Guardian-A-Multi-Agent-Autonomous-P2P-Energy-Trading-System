'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Cpu,
  Radio,
  Thermometer,
  Wifi,
  WifiOff,
  Activity,
  HardDrive,
  Lightbulb,
  Power,
  AlertTriangle,
  CheckCircle2,
  Router,
} from 'lucide-react';

interface EdgeStatus {
  is_connected: boolean;
  mode: 'simulation' | 'hardware' | 'hybrid';
  runtime_status: 'running' | 'stopped' | 'error' | 'initializing';
  sensor_status: 'ok' | 'degraded' | 'offline';
  relay_state: 'on' | 'off' | 'safe_mode';
  telemetry_status: 'streaming' | 'buffered' | 'offline';
  last_heartbeat?: number;
  device_info?: {
    device_id: string;
    firmware_version?: string;
    uptime_seconds?: number;
    cpu_temp_c?: number;
    memory_percent?: number;
  };
}

interface EdgeStatusPanelProps {
  status: EdgeStatus | null;
  isLoading?: boolean;
}

const getModeColor = (mode: EdgeStatus['mode']) => {
  switch (mode) {
    case 'simulation':
      return 'bg-blue-100 text-blue-800';
    case 'hardware':
      return 'bg-green-100 text-green-800';
    case 'hybrid':
      return 'bg-purple-100 text-purple-800';
    default:
      return 'bg-gray-100 text-gray-800';
  }
};

const getStatusColor = (status: string) => {
  switch (status) {
    case 'running':
    case 'ok':
    case 'streaming':
    case 'on':
      return 'text-green-500';
    case 'stopped':
    case 'buffered':
    case 'off':
      return 'text-gray-500';
    case 'degraded':
    case 'safe_mode':
      return 'text-yellow-500';
    case 'error':
    case 'offline':
      return 'text-red-500';
    default:
      return 'text-gray-500';
  }
};

const getStatusIcon = (status: string) => {
  switch (status) {
    case 'running':
    case 'ok':
    case 'streaming':
      return <CheckCircle2 className="h-4 w-4" />;
    case 'error':
    case 'offline':
      return <AlertTriangle className="h-4 w-4" />;
    default:
      return <Activity className="h-4 w-4" />;
  }
};

const formatUptime = (seconds: number) => {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
};

export function EdgeStatusPanel({ status, isLoading }: EdgeStatusPanelProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base font-medium">
            <Cpu className="h-4 w-4" />
            Edge Runtime
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="animate-pulse space-y-3">
            <div className="h-4 bg-muted rounded w-3/4" />
            <div className="h-4 bg-muted rounded w-1/2" />
            <div className="h-4 bg-muted rounded w-2/3" />
          </div>
        </CardContent>
      </Card>
    );
  }

  const mockStatus: EdgeStatus = status || {
    is_connected: true,
    mode: 'simulation',
    runtime_status: 'running',
    sensor_status: 'ok',
    relay_state: 'off',
    telemetry_status: 'streaming',
    device_info: {
      device_id: 'pi-sim-001',
      firmware_version: '2.1.0',
      uptime_seconds: 3600,
      cpu_temp_c: 45,
      memory_percent: 35,
    },
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base font-medium">
            <Cpu className="h-4 w-4" />
            Edge / Raspberry Pi
          </CardTitle>
          {mockStatus.is_connected ? (
            <Badge variant="outline" className="bg-green-50 text-green-700 border-green-200">
              <Wifi className="h-3 w-3 mr-1" />
              Connected
            </Badge>
          ) : (
            <Badge variant="outline" className="bg-red-50 text-red-700 border-red-200">
              <WifiOff className="h-3 w-3 mr-1" />
              Disconnected
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Mode */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Router className="h-4 w-4" />
            <span className="text-sm">Mode</span>
          </div>
          <Badge className={getModeColor(mockStatus.mode)}>
            {mockStatus.mode.toUpperCase()}
          </Badge>
        </div>

        {/* Runtime Status */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Activity className="h-4 w-4" />
            <span className="text-sm">Runtime</span>
          </div>
          <div className={`flex items-center gap-1 ${getStatusColor(mockStatus.runtime_status)}`}>
            {getStatusIcon(mockStatus.runtime_status)}
            <span className="text-sm font-medium capitalize">{mockStatus.runtime_status}</span>
          </div>
        </div>

        {/* Sensor Status */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Radio className="h-4 w-4" />
            <span className="text-sm">Sensor Input</span>
          </div>
          <div className={`flex items-center gap-1 ${getStatusColor(mockStatus.sensor_status)}`}>
            {getStatusIcon(mockStatus.sensor_status)}
            <span className="text-sm font-medium capitalize">{mockStatus.sensor_status}</span>
          </div>
        </div>

        {/* Relay/LED State */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Lightbulb className="h-4 w-4" />
            <span className="text-sm">Relay / LED</span>
          </div>
          <div className={`flex items-center gap-1 ${getStatusColor(mockStatus.relay_state)}`}>
            <Power className="h-4 w-4" />
            <span className="text-sm font-medium capitalize">{mockStatus.relay_state.replace('_', ' ')}</span>
          </div>
        </div>

        {/* Telemetry Status */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-muted-foreground">
            <HardDrive className="h-4 w-4" />
            <span className="text-sm">Telemetry</span>
          </div>
          <div className={`flex items-center gap-1 ${getStatusColor(mockStatus.telemetry_status)}`}>
            {getStatusIcon(mockStatus.telemetry_status)}
            <span className="text-sm font-medium capitalize">{mockStatus.telemetry_status}</span>
          </div>
        </div>

        {/* Device Info */}
        {mockStatus.device_info && (
          <div className="pt-2 border-t">
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-muted-foreground">Device:</span>
                <span className="ml-1 font-mono">{mockStatus.device_info.device_id}</span>
              </div>
              {mockStatus.device_info.firmware_version && (
                <div>
                  <span className="text-muted-foreground">Firmware:</span>
                  <span className="ml-1 font-mono">v{mockStatus.device_info.firmware_version}</span>
                </div>
              )}
              {mockStatus.device_info.uptime_seconds && (
                <div>
                  <span className="text-muted-foreground">Uptime:</span>
                  <span className="ml-1">{formatUptime(mockStatus.device_info.uptime_seconds)}</span>
                </div>
              )}
              {mockStatus.device_info.cpu_temp_c && (
                <div className="flex items-center gap-1">
                  <Thermometer className="h-3 w-3 text-muted-foreground" />
                  <span>{mockStatus.device_info.cpu_temp_c}°C</span>
                </div>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default EdgeStatusPanel;
