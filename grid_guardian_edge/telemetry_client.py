"""
Grid-Guardian Edge - Telemetry Client
Sends sensor data to backend via HTTP and/or MQTT
"""

import json
import logging
import time
from collections import deque
from typing import Any, Dict, Optional

import requests

from config import (
    BACKEND_URL,
    BACKEND_TIMEOUT,
    BACKEND_RETRY_ATTEMPTS,
    BACKEND_RETRY_DELAY,
    NODE_ID,
    API_TELEMETRY,
    MQTT_ENABLED,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_KEEPALIVE,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    MQTT_TOPIC_TELEMETRY,
    TELEMETRY_BUFFER_SIZE,
)

logger = logging.getLogger(__name__)


class TelemetryClient:
    """
    Send telemetry data to Grid-Guardian backend.
    Supports HTTP POST (primary) and MQTT (optional/redundant).
    Includes retry mechanism and offline buffering.
    """

    def __init__(self):
        self.http_endpoint = f"{BACKEND_URL}{API_TELEMETRY}"
        self.mqtt_client = None
        self.mqtt_connected = False

        # Offline buffer for when backend is unreachable
        self.buffer = deque(maxlen=TELEMETRY_BUFFER_SIZE)

        # Statistics
        self.stats = {
            "http_success": 0,
            "http_failures": 0,
            "mqtt_success": 0,
            "mqtt_failures": 0,
            "buffered_count": 0,
            "buffer_flushes": 0,
        }

        # Initialize MQTT if enabled
        if MQTT_ENABLED:
            self._init_mqtt()

    def _init_mqtt(self):
        """Initialize MQTT client"""
        try:
            import paho.mqtt.client as mqtt

            client_id = f"gridguardian-edge-{NODE_ID}"
            self.mqtt_client = mqtt.Client(client_id=client_id)

            # Set callbacks
            self.mqtt_client.on_connect = self._on_mqtt_connect
            self.mqtt_client.on_disconnect = self._on_mqtt_disconnect

            # Set credentials if provided
            if MQTT_USERNAME and MQTT_PASSWORD:
                self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

            # Connect asynchronously
            try:
                self.mqtt_client.connect_async(
                    MQTT_BROKER,
                    MQTT_PORT,
                    MQTT_KEEPALIVE
                )
                self.mqtt_client.loop_start()
                logger.info(f"MQTT client initialized, connecting to {MQTT_BROKER}:{MQTT_PORT}")
            except Exception as e:
                logger.warning(f"MQTT connection failed: {e}")

        except ImportError:
            logger.warning("paho-mqtt not installed. MQTT disabled.")
            self.mqtt_client = None

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connection callback"""
        if rc == 0:
            self.mqtt_connected = True
            logger.info("MQTT connected successfully")
        else:
            self.mqtt_connected = False
            logger.warning(f"MQTT connection failed with code: {rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback"""
        self.mqtt_connected = False
        if rc != 0:
            logger.warning(f"MQTT disconnected unexpectedly: {rc}")

    def _build_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Build telemetry payload including AI decision fields"""
        payload = {
            "node_id": NODE_ID,
            "voltage": data.get("voltage"),
            "current": data.get("current"),
            "power": data.get("power"),
            "energy": data.get("energy"),
            "frequency": data.get("frequency"),
            "power_factor": data.get("power_factor"),
            "relay_state": data.get("relay_state", False),
            "source": data.get("source", "unknown"),
            "valid": data.get("valid", True),
            "timestamp": data.get("timestamp", time.time()),
        }

        # Include AI decision fields so the dashboard can display them
        ai_fields = ["ai_decision", "ai_confidence", "ai_source", "model_key", "condition"]
        for field in ai_fields:
            if field in data and data[field] is not None:
                payload[field] = data[field]

        return payload

    def _send_http(self, payload: Dict[str, Any]) -> bool:
        """Send telemetry via HTTP POST with retry"""
        for attempt in range(BACKEND_RETRY_ATTEMPTS):
            try:
                response = requests.post(
                    self.http_endpoint,
                    json=payload,
                    timeout=BACKEND_TIMEOUT,
                    headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                self.stats["http_success"] += 1
                return True

            except requests.exceptions.Timeout:
                logger.warning(f"HTTP timeout (attempt {attempt + 1}/{BACKEND_RETRY_ATTEMPTS})")
            except requests.exceptions.ConnectionError:
                logger.warning(f"HTTP connection error (attempt {attempt + 1}/{BACKEND_RETRY_ATTEMPTS})")
            except requests.exceptions.RequestException as e:
                logger.warning(f"HTTP error: {e} (attempt {attempt + 1}/{BACKEND_RETRY_ATTEMPTS})")

            # Wait before retry (except on last attempt)
            if attempt < BACKEND_RETRY_ATTEMPTS - 1:
                time.sleep(BACKEND_RETRY_DELAY)

        self.stats["http_failures"] += 1
        return False

    def _send_mqtt(self, payload: Dict[str, Any]) -> bool:
        """Send telemetry via MQTT"""
        if not self.mqtt_client or not self.mqtt_connected:
            return False

        try:
            message = json.dumps(payload)
            result = self.mqtt_client.publish(
                MQTT_TOPIC_TELEMETRY,
                message,
                qos=1  # At least once delivery
            )

            if result.rc == 0:
                self.stats["mqtt_success"] += 1
                return True
            else:
                logger.warning(f"MQTT publish failed with rc: {result.rc}")
                self.stats["mqtt_failures"] += 1
                return False

        except Exception as e:
            logger.error(f"MQTT send error: {e}")
            self.stats["mqtt_failures"] += 1
            return False

    def _buffer_telemetry(self, payload: Dict[str, Any]):
        """Buffer telemetry for later transmission"""
        self.buffer.append(payload)
        self.stats["buffered_count"] += 1
        logger.debug(f"Telemetry buffered. Buffer size: {len(self.buffer)}")

    def _flush_buffer(self):
        """Attempt to send buffered telemetry"""
        if not self.buffer:
            return

        flushed = 0
        while self.buffer:
            payload = self.buffer[0]
            if self._send_http(payload):
                self.buffer.popleft()
                flushed += 1
            else:
                # Stop flushing if backend is still unreachable
                break

        if flushed > 0:
            self.stats["buffer_flushes"] += 1
            logger.info(f"Flushed {flushed} buffered telemetry records")

    def send_telemetry(self, data: Dict[str, Any]) -> bool:
        """
        Send telemetry data to backend.

        Args:
            data: Sensor reading dictionary

        Returns:
            True if sent successfully via at least one channel
        """
        payload = self._build_payload(data)

        # Try to flush buffer first
        self._flush_buffer()

        # Send via HTTP (primary)
        http_success = self._send_http(payload)

        # Send via MQTT (secondary/redundant)
        mqtt_success = False
        if MQTT_ENABLED:
            mqtt_success = self._send_mqtt(payload)

        # Buffer if both channels failed
        if not http_success and not mqtt_success:
            self._buffer_telemetry(payload)
            return False

        return True

    def send_status(self, status: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """Send status update to backend"""
        payload = {
            "node_id": NODE_ID,
            "status": status,
            "details": details or {},
            "timestamp": time.time(),
        }

        # Try HTTP
        try:
            response = requests.post(
                f"{BACKEND_URL}/api/status",
                json=payload,
                timeout=BACKEND_TIMEOUT
            )
            return response.status_code == 200
        except Exception:
            pass

        # Try MQTT
        if self.mqtt_client and self.mqtt_connected:
            try:
                from config import MQTT_TOPIC_STATUS
                self.mqtt_client.publish(MQTT_TOPIC_STATUS, json.dumps(payload), qos=1)
                return True
            except Exception:
                pass

        return False

    def send_alert(self, alert_type: str, message: str, severity: str = "warning") -> bool:
        """Send alert to backend"""
        payload = {
            "node_id": NODE_ID,
            "alert_type": alert_type,
            "message": message,
            "severity": severity,
            "timestamp": time.time(),
        }

        # Try HTTP
        try:
            response = requests.post(
                f"{BACKEND_URL}/api/alerts",
                json=payload,
                timeout=BACKEND_TIMEOUT
            )
            if response.status_code == 200:
                return True
        except Exception:
            pass

        # Try MQTT
        if self.mqtt_client and self.mqtt_connected:
            try:
                from config import MQTT_TOPIC_ALERTS
                self.mqtt_client.publish(MQTT_TOPIC_ALERTS, json.dumps(payload), qos=1)
                return True
            except Exception:
                pass

        return False

    def get_stats(self) -> Dict[str, Any]:
        """Get telemetry client statistics"""
        return {
            **self.stats,
            "buffer_size": len(self.buffer),
            "mqtt_connected": self.mqtt_connected,
            "mqtt_enabled": MQTT_ENABLED,
        }

    def close(self):
        """Close connections and cleanup"""
        if self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
                logger.info("MQTT client disconnected")
            except Exception as e:
                logger.error(f"Error closing MQTT client: {e}")
