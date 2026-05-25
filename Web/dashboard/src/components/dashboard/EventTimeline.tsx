'use client';

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Sun,
  Battery,
  Brain,
  ArrowRightLeft,
  FileCheck,
  AlertCircle,
  Zap,
  Clock,
  Radio,
  CheckCircle2,
  XCircle,
} from 'lucide-react';

interface TimelineEvent {
  id: string;
  type: 'sensing' | 'condition' | 'model_selection' | 'action' | 'trade' | 'settlement' | 'edge' | 'alert';
  timestamp: number;
  title: string;
  description: string;
  prosumer_id?: string;
  metadata?: Record<string, any>;
}

interface EventTimelineProps {
  events: TimelineEvent[];
  maxHeight?: string;
}

const getEventIcon = (type: TimelineEvent['type']) => {
  switch (type) {
    case 'sensing':
      return <Radio className="h-4 w-4" />;
    case 'condition':
      return <Zap className="h-4 w-4" />;
    case 'model_selection':
      return <Brain className="h-4 w-4" />;
    case 'action':
      return <Battery className="h-4 w-4" />;
    case 'trade':
      return <ArrowRightLeft className="h-4 w-4" />;
    case 'settlement':
      return <FileCheck className="h-4 w-4" />;
    case 'edge':
      return <Sun className="h-4 w-4" />;
    case 'alert':
      return <AlertCircle className="h-4 w-4" />;
    default:
      return <Clock className="h-4 w-4" />;
  }
};

const getEventColor = (type: TimelineEvent['type']) => {
  switch (type) {
    case 'sensing':
      return 'bg-blue-500';
    case 'condition':
      return 'bg-yellow-500';
    case 'model_selection':
      return 'bg-purple-500';
    case 'action':
      return 'bg-green-500';
    case 'trade':
      return 'bg-orange-500';
    case 'settlement':
      return 'bg-emerald-500';
    case 'edge':
      return 'bg-cyan-500';
    case 'alert':
      return 'bg-red-500';
    default:
      return 'bg-gray-500';
  }
};

const getEventBadgeVariant = (type: TimelineEvent['type']) => {
  switch (type) {
    case 'alert':
      return 'destructive';
    case 'settlement':
      return 'default';
    default:
      return 'secondary';
  }
};

// Client-side only time formatter to avoid hydration mismatch
const useHydrated = () => {
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    setHydrated(true);
  }, []);
  return hydrated;
};

const formatEventTime = (timestamp: number, hydrated: boolean) => {
  if (!hydrated) {
    return '--:--:--'; // Consistent placeholder during SSR
  }
  const date = new Date(timestamp);
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
};

export function EventTimeline({ events, maxHeight = '400px' }: EventTimelineProps) {
  const hydrated = useHydrated();
  const sortedEvents = [...events].sort((a, b) => b.timestamp - a.timestamp);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base font-medium">
          <Clock className="h-4 w-4" />
          Event Timeline
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Decision flow from sensing to settlement
        </p>
      </CardHeader>
      <CardContent>
        <ScrollArea className="pr-4" style={{ height: maxHeight }}>
          <div className="relative">
            {/* Timeline line */}
            <div className="absolute left-[15px] top-0 bottom-0 w-0.5 bg-muted" />

            <div className="space-y-4">
              {sortedEvents.length > 0 ? (
                sortedEvents.map((event, idx) => (
                  <div key={event.id} className="relative flex items-start gap-3">
                    {/* Timeline dot */}
                    <div className={`relative z-10 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full ${getEventColor(event.type)} text-white`}>
                      {getEventIcon(event.type)}
                    </div>

                    {/* Event content */}
                    <div className="flex-1 min-w-0 pb-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-sm">{event.title}</span>
                        <Badge variant={getEventBadgeVariant(event.type)} className="text-xs">
                          {event.type.replace(/_/g, ' ')}
                        </Badge>
                        {event.prosumer_id && (
                          <Badge variant="outline" className="text-xs">
                            {event.prosumer_id}
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {event.description}
                      </p>
                      <p className="text-xs text-muted-foreground/60 mt-0.5">
                        {formatEventTime(event.timestamp, hydrated)}
                      </p>

                      {/* Metadata */}
                      {event.metadata && Object.keys(event.metadata).length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {Object.entries(event.metadata).map(([key, value]) => (
                            <span key={key} className="text-xs bg-muted px-1.5 py-0.5 rounded">
                              {key}: {typeof value === 'number' ? value.toFixed(2) : String(value)}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <Clock className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">No events yet</p>
                  <p className="text-xs">Events will appear as the demo runs</p>
                </div>
              )}
            </div>
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}

export default EventTimeline;
