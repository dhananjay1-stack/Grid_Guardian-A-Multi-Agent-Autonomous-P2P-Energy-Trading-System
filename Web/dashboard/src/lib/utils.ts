import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatTimestamp(timestamp: number | undefined | null): string {
  if (timestamp === undefined || timestamp === null || isNaN(timestamp)) {
    return 'N/A';
  }
  const date = new Date(timestamp * 1000);
  return date.toLocaleString();
}

export function formatRelativeTime(timestamp: number | undefined | null): string {
  if (timestamp === undefined || timestamp === null || isNaN(timestamp)) {
    return 'N/A';
  }
  const now = Date.now();
  const diff = now - timestamp * 1000;

  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (days > 0) return `${days}d ago`;
  if (hours > 0) return `${hours}h ago`;
  if (minutes > 0) return `${minutes}m ago`;
  return `${seconds}s ago`;
}

export function formatPower(watts: number | undefined | null): string {
  if (watts === undefined || watts === null || isNaN(watts)) {
    return '0 W';
  }
  if (watts >= 1000000) {
    return `${(watts / 1000000).toFixed(2)} MW`;
  }
  if (watts >= 1000) {
    return `${(watts / 1000).toFixed(2)} kW`;
  }
  return `${watts.toFixed(1)} W`;
}

export function formatVoltage(volts: number | undefined | null): string {
  if (volts === undefined || volts === null || isNaN(volts)) {
    return '0 V';
  }
  return `${volts.toFixed(1)} V`;
}

export function formatCurrent(amps: number | undefined | null): string {
  if (amps === undefined || amps === null || isNaN(amps)) {
    return '0 A';
  }
  return `${amps.toFixed(2)} A`;
}

export function formatConfidence(confidence: number | undefined | null): string {
  if (confidence === undefined || confidence === null || isNaN(confidence)) {
    return '0%';
  }
  return `${(confidence * 100).toFixed(1)}%`;
}

export function formatEnergy(kWh: number | string): string {
  const numKWh = typeof kWh === 'string' ? parseFloat(kWh) : kWh;
  if (isNaN(numKWh)) return '0.000 kWh';
  
  if (numKWh >= 1000) {
    return `${(numKWh / 1000).toFixed(2)} MWh`;
  }
  return `${numKWh.toFixed(3)} kWh`;
}

export function formatPrice(price: number | string | undefined | null): string {
  if (price === undefined || price === null) return '$0.0000';
  const numPrice = typeof price === 'string' ? parseFloat(price) : price;
  if (isNaN(numPrice)) return '$0.0000';
  return `$${numPrice.toFixed(4)}`;
}

export function getStatusColor(status: string | undefined | null): string {
  if (!status) return 'text-gray-500';
  switch (status.toUpperCase()) {
    case 'ACTIVE':
    case 'OK':
    case 'CONFIRMED':
    case 'SETTLED':
    case 'EXECUTED':
      return 'text-green-500';
    case 'PENDING':
    case 'MATCHED':
      return 'text-yellow-500';
    case 'IDLE':
    case 'DEGRADED':
      return 'text-orange-500';
    case 'OFFLINE':
    case 'ERROR':
    case 'FAILED':
    case 'DISPUTED':
      return 'text-red-500';
    default:
      return 'text-gray-500';
  }
}

export function getStatusBgColor(status: string | undefined | null): string {
  if (!status) return 'bg-gray-500/10 text-gray-500 border-gray-500/20';
  switch (status.toUpperCase()) {
    case 'ACTIVE':
    case 'OK':
    case 'CONFIRMED':
    case 'SETTLED':
    case 'EXECUTED':
      return 'bg-green-500/10 text-green-500 border-green-500/20';
    case 'PENDING':
    case 'MATCHED':
      return 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20';
    case 'IDLE':
    case 'DEGRADED':
      return 'bg-orange-500/10 text-orange-500 border-orange-500/20';
    case 'OFFLINE':
    case 'ERROR':
    case 'FAILED':
    case 'DISPUTED':
      return 'bg-red-500/10 text-red-500 border-red-500/20';
    default:
      return 'bg-gray-500/10 text-gray-500 border-gray-500/20';
  }
}

export function getDecisionColor(decision: string | undefined | null): string {
  if (!decision) return 'text-gray-500';
  switch (decision.toUpperCase()) {
    case 'BUY':
      return 'text-blue-500';
    case 'SELL':
      return 'text-green-500';
    case 'HOLD':
      return 'text-yellow-500';
    default:
      return 'text-gray-500';
  }
}

export function getDecisionBgColor(decision: string | undefined | null): string {
  if (!decision) return 'bg-gray-500/10 text-gray-500 border-gray-500/20';
  switch (decision.toUpperCase()) {
    case 'BUY':
      return 'bg-blue-500/10 text-blue-500 border-blue-500/20';
    case 'SELL':
      return 'bg-green-500/10 text-green-500 border-green-500/20';
    case 'HOLD':
      return 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20';
    default:
      return 'bg-gray-500/10 text-gray-500 border-gray-500/20';
  }
}
