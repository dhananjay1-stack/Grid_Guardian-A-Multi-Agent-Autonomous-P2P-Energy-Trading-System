'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useControlStore } from '@/store';
import { controlService } from '@/services';
import {
  Power,
  PowerOff,
  Settings,
  RefreshCw,
  AlertTriangle,
  Shield,
  ShieldOff,
  CheckCircle,
  XCircle,
} from 'lucide-react';

interface ControlPanelProps {
  onRefresh?: () => void;
}

export function ControlPanel({ onRefresh }: ControlPanelProps) {
  const {
    trading_enabled,
    manual_override,
    safe_mode,
    last_action,
    pendingAction,
    setTradingEnabled,
    setManualOverride,
    setSafeMode,
    setLastAction,
    setPendingAction,
    setError,
  } = useControlStore();

  const [confirmAction, setConfirmAction] = useState<string | null>(null);

  const handleEnableTrading = async () => {
    try {
      setPendingAction('enable_trading');
      await controlService.enableTrading();
      setTradingEnabled(true);
      setLastAction('Trading enabled', true);
    } catch (error) {
      setError('Failed to enable trading');
      setLastAction('Trading enable failed', false);
    }
  };

  const handleDisableTrading = async () => {
    if (confirmAction !== 'disable_trading') {
      setConfirmAction('disable_trading');
      return;
    }
    try {
      setPendingAction('disable_trading');
      await controlService.disableTrading();
      setTradingEnabled(false);
      setLastAction('Trading disabled', true);
      setConfirmAction(null);
    } catch (error) {
      setError('Failed to disable trading');
      setLastAction('Trading disable failed', false);
    }
  };

  const handleManualOverride = async () => {
    if (!manual_override && confirmAction !== 'manual_override') {
      setConfirmAction('manual_override');
      return;
    }
    try {
      setPendingAction('manual_override');
      await controlService.setManualOverride(!manual_override);
      setManualOverride(!manual_override);
      setLastAction(`Manual override ${!manual_override ? 'enabled' : 'disabled'}`, true);
      setConfirmAction(null);
    } catch (error) {
      setError('Failed to toggle manual override');
      setLastAction('Manual override toggle failed', false);
    }
  };

  const handleSafeMode = async () => {
    try {
      setPendingAction('safe_mode');
      if (safe_mode) {
        await controlService.disableSafeMode();
        setSafeMode(false);
        setLastAction('Safe mode disabled', true);
      } else {
        await controlService.enableSafeMode();
        setSafeMode(true);
        setLastAction('Safe mode enabled', true);
      }
    } catch (error) {
      setError('Failed to toggle safe mode');
      setLastAction('Safe mode toggle failed', false);
    }
  };

  const handleRefresh = async () => {
    try {
      setPendingAction('refresh');
      await controlService.refresh();
      setLastAction('System refreshed', true);
      onRefresh?.();
    } catch (error) {
      setError('Failed to refresh');
      setLastAction('Refresh failed', false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base font-medium">
          <Settings className="h-4 w-4" />
          Control Panel
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Status Indicators */}
        <div className="flex flex-wrap gap-2">
          <Badge variant={trading_enabled ? 'success' : 'error'}>
            {trading_enabled ? 'Trading ON' : 'Trading OFF'}
          </Badge>
          {manual_override && (
            <Badge variant="warning">Manual Override</Badge>
          )}
          {safe_mode && (
            <Badge variant="error">Safe Mode</Badge>
          )}
        </div>

        {/* Trading Control */}
        <div className="space-y-2">
          <div className="text-sm font-medium text-muted-foreground">Trading</div>
          <div className="grid grid-cols-2 gap-2">
            <Button
              variant={trading_enabled ? 'outline' : 'success'}
              size="sm"
              onClick={handleEnableTrading}
              disabled={trading_enabled || pendingAction === 'enable_trading'}
              loading={pendingAction === 'enable_trading'}
            >
              <Power className="mr-2 h-4 w-4" />
              Enable
            </Button>
            <Button
              variant={confirmAction === 'disable_trading' ? 'destructive' : 'outline'}
              size="sm"
              onClick={handleDisableTrading}
              disabled={!trading_enabled || pendingAction === 'disable_trading'}
              loading={pendingAction === 'disable_trading'}
            >
              <PowerOff className="mr-2 h-4 w-4" />
              {confirmAction === 'disable_trading' ? 'Confirm?' : 'Disable'}
            </Button>
          </div>
        </div>

        {/* Manual Override */}
        <div className="space-y-2">
          <div className="text-sm font-medium text-muted-foreground">Override</div>
          <Button
            variant={confirmAction === 'manual_override' ? 'warning' : (manual_override ? 'warning' : 'outline')}
            size="sm"
            className="w-full"
            onClick={handleManualOverride}
            loading={pendingAction === 'manual_override'}
          >
            <AlertTriangle className="mr-2 h-4 w-4" />
            {confirmAction === 'manual_override'
              ? 'Confirm Override?'
              : (manual_override ? 'Disable Override' : 'Enable Override')}
          </Button>
        </div>

        {/* Safe Mode / Emergency */}
        <div className="space-y-2">
          <div className="text-sm font-medium text-muted-foreground">Emergency</div>
          <Button
            variant={safe_mode ? 'outline' : 'destructive'}
            size="sm"
            className="w-full"
            onClick={handleSafeMode}
            loading={pendingAction === 'safe_mode'}
          >
            {safe_mode ? (
              <>
                <ShieldOff className="mr-2 h-4 w-4" />
                Exit Safe Mode
              </>
            ) : (
              <>
                <Shield className="mr-2 h-4 w-4" />
                Activate Safe Mode
              </>
            )}
          </Button>
        </div>

        {/* Refresh */}
        <div className="pt-2 border-t">
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={handleRefresh}
            loading={pendingAction === 'refresh'}
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh Data
          </Button>
        </div>

        {/* Last Action Feedback */}
        {last_action && (
          <div className={`flex items-center gap-2 rounded-lg p-2 text-sm ${
            last_action.success
              ? 'bg-green-500/10 text-green-500'
              : 'bg-red-500/10 text-red-500'
          }`}>
            {last_action.success ? (
              <CheckCircle className="h-4 w-4" />
            ) : (
              <XCircle className="h-4 w-4" />
            )}
            <span>{last_action.action}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
