"""Structured logging for AI service."""
from __future__ import annotations

import logging
import sys
import json
from datetime import datetime
from typing import Any, Dict, Optional
from functools import lru_cache


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if hasattr(record, "extra_data"):
            log_data.update(record.extra_data)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class AIServiceLogger(logging.Logger):
    """Extended logger with structured data support."""

    def structured(self, level: int, msg: str, extra_data: Optional[Dict[str, Any]] = None, **kwargs):
        """Log with structured extra data."""
        if extra_data:
            kwargs["extra"] = {"extra_data": extra_data}
        self.log(level, msg, **kwargs)

    def inference_log(self, node_id: str, policy: str, decision: str,
                      confidence: float, latency_ms: float, **extra):
        """Log inference request with standard fields."""
        self.structured(logging.INFO, f"Inference: {node_id} -> {decision}", {
            "node_id": node_id,
            "selected_policy": policy,
            "decision": decision,
            "confidence": confidence,
            "latency_ms": latency_ms,
            **extra
        })

    def safety_log(self, action_type: str, original: Any, modified: Any, reason: str):
        """Log safety intervention."""
        self.structured(logging.WARNING, f"Safety intervention: {reason}", {
            "event_type": "safety_intervention",
            "action_type": action_type,
            "original_value": original,
            "modified_value": modified,
            "reason": reason
        })

    def policy_switch(self, from_policy: str, to_policy: str, reason: str):
        """Log policy switch event."""
        self.structured(logging.INFO, f"Policy switch: {from_policy} -> {to_policy}", {
            "event_type": "policy_switch",
            "from_policy": from_policy,
            "to_policy": to_policy,
            "reason": reason
        })


@lru_cache()
def get_logger(name: str = "ai_service") -> AIServiceLogger:
    """Get or create a structured logger."""
    logging.setLoggerClass(AIServiceLogger)
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger
