"""
Structured logging for AI Decision Engine.
"""
import logging
import sys
import json
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.config import settings


class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, "extra"):
            log_entry.update(record.extra)

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class StructuredLogger(logging.Logger):
    """Extended logger with structured logging support."""

    def _log_with_extra(
        self,
        level: int,
        msg: str,
        extra: Optional[Dict[str, Any]] = None,
        *args,
        **kwargs
    ):
        """Log with extra structured data."""
        if extra:
            kwargs["extra"] = {"extra": extra}
        super()._log(level, msg, args, **kwargs)

    def info_struct(self, msg: str, **extra):
        """Log info with structured data."""
        self._log_with_extra(logging.INFO, msg, extra)

    def debug_struct(self, msg: str, **extra):
        """Log debug with structured data."""
        self._log_with_extra(logging.DEBUG, msg, extra)

    def warning_struct(self, msg: str, **extra):
        """Log warning with structured data."""
        self._log_with_extra(logging.WARNING, msg, extra)

    def error_struct(self, msg: str, **extra):
        """Log error with structured data."""
        self._log_with_extra(logging.ERROR, msg, extra)


def get_logger(name: str) -> StructuredLogger:
    """Get a configured logger instance."""
    logging.setLoggerClass(StructuredLogger)
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)

        # Use JSON formatter in production, simple format in development
        if settings.ENVIRONMENT == "production":
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )
            )

        logger.addHandler(handler)
        logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))

    return logger


# Metrics tracking
class MetricsTracker:
    """Simple in-memory metrics tracker."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset all metrics."""
        self._metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_latency_ms": 0.0,
            "model_selections": {"bc": 0, "cql": 0, "dt": 0},
            "safety_blocks": 0,
            "fallback_events": 0,
            "decision_counts": {"BUY": 0, "SELL": 0, "HOLD": 0, "CHARGE": 0, "DISCHARGE": 0},
        }

    def record_request(self, success: bool, latency_ms: float):
        """Record a request."""
        self._metrics["total_requests"] += 1
        self._metrics["total_latency_ms"] += latency_ms
        if success:
            self._metrics["successful_requests"] += 1
        else:
            self._metrics["failed_requests"] += 1

    def record_model_selection(self, model: str):
        """Record model selection."""
        model = model.lower()
        if model in self._metrics["model_selections"]:
            self._metrics["model_selections"][model] += 1

    def record_safety_block(self):
        """Record a safety block event."""
        self._metrics["safety_blocks"] += 1

    def record_fallback(self):
        """Record a fallback event."""
        self._metrics["fallback_events"] += 1

    def record_decision(self, decision: str):
        """Record a decision."""
        if decision in self._metrics["decision_counts"]:
            self._metrics["decision_counts"][decision] += 1

    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics."""
        metrics = self._metrics.copy()
        if metrics["total_requests"] > 0:
            metrics["avg_latency_ms"] = (
                metrics["total_latency_ms"] / metrics["total_requests"]
            )
        else:
            metrics["avg_latency_ms"] = 0.0
        return metrics


# Global metrics instance
metrics_tracker = MetricsTracker()
