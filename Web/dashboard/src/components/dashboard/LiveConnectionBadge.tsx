'use client';

import { useSystemStore } from '@/store';
import { Wifi, WifiOff } from 'lucide-react';

export function LiveConnectionBadge() {
  const { isConnected, lastConnectedAt } = useSystemStore();

  return (
    <div className={`flex items-center gap-2 rounded-full px-3 py-1 text-sm ${
      isConnected
        ? 'bg-green-500/10 text-green-500'
        : 'bg-red-500/10 text-red-500'
    }`}>
      {isConnected ? (
        <>
          <Wifi className="h-4 w-4 animate-pulse" />
          <span>Live</span>
        </>
      ) : (
        <>
          <WifiOff className="h-4 w-4" />
          <span>Disconnected</span>
        </>
      )}
    </div>
  );
}
