"""
Grid-Guardian Edge - Command Listener
Receives commands from backend via HTTP polling and/or MQTT subscription
"""

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

import requests

from config import (
    BACKEND_URL,
    BACKEND_TIMEOUT,
    NODE_ID,
    COMMAND_POLL_INTERVAL,
    MQTT_ENABLED,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_KEEPALIVE,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    MQTT_TOPIC_COMMANDS,
)

logger = logging.getLogger(__name__)


class CommandListener:
    """
    Listen for commands from Grid-Guardian backend.
    Supports both HTTP polling and MQTT subscription.
    Sends ACK for received commands.
    """

    # Supported command types
    VALID_COMMANDS = {
        "relay_on",
        "relay_off",
        "ping",
        "shutdown",
        "safe_mode",
        "hold",
        "execute_trade",
        "discharge",
        "charge",
        "post_receipt",
        "heartbeat_check",
        "settlement_complete",
        "delivery_confirmed",
    }

    def __init__(self, action_callback: Callable[[Dict[str, Any]], None]):
        """
        Initialize command listener.

        Args:
            action_callback: Function to call when command is received.
                            Receives command dict with 'command_type' and 'payload'.
        """
        self.action_callback = action_callback
        self.http_endpoint = f"{BACKEND_URL}/api/commands/{NODE_ID}"
        self.ack_endpoint = f"{BACKEND_URL}/api/commands/{NODE_ID}/ack"

        self.running = False
        self.poll_thread = None
        self.mqtt_client = None
        self.mqtt_connected = False

        # Track processed commands for idempotency
        self.processed_commands = set()
        self.processed_commands_max = 1000

        # Statistics
        self.stats = {
            "commands_received": 0,
            "commands_executed": 0,
            "commands_failed": 0,
            "acks_sent": 0,
            "duplicates_ignored": 0,
        }

        # Initialize MQTT if enabled
        if MQTT_ENABLED:
            self._init_mqtt()

    def _init_mqtt(self):
        """Initialize MQTT client for command subscription"""
        try:
            import paho.mqtt.client as mqtt

            client_id = f"gridguardian-cmd-{NODE_ID}"
            self.mqtt_client = mqtt.Client(client_id=client_id)

            # Set callbacks
            self.mqtt_client.on_connect = self._on_mqtt_connect
            self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
            self.mqtt_client.on_message = self._on_mqtt_message

            # Set credentials if provided
            if MQTT_USERNAME and MQTT_PASSWORD:
                self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        except ImportError:
            logger.warning("paho-mqtt not installed. MQTT command listening disabled.")
            self.mqtt_client = None

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connection callback"""
        if rc == 0:
            self.mqtt_connected = True
            # Subscribe to commands topic
            client.subscribe(MQTT_TOPIC_COMMANDS, qos=1)
            logger.info(f"MQTT connected. Subscribed to {MQTT_TOPIC_COMMANDS}")
        else:
            self.mqtt_connected = False
            logger.warning(f"MQTT connection failed with code: {rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback"""
        self.mqtt_connected = False
        if rc != 0:
            logger.warning(f"MQTT disconnected unexpectedly: {rc}")

    def _on_mqtt_message(self, client, userdata, msg):
        """MQTT message callback"""
        try:
            payload = json.loads(msg.payload.decode())
            logger.debug(f"MQTT command received: {payload}")
            self._process_command(payload, source="mqtt")
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in MQTT message: {msg.payload}")
        except Exception as e:
            logger.error(f"Error processing MQTT command: {e}")

    def _is_duplicate(self, command_id: str) -> bool:
        """Check if command was already processed"""
        if command_id in self.processed_commands:
            return True
        return False

    def _mark_processed(self, command_id: str):
        """Mark command as processed"""
        self.processed_commands.add(command_id)
        # Clean up old entries if set is too large
        if len(self.processed_commands) > self.processed_commands_max:
            # Remove oldest half
            to_remove = list(self.processed_commands)[:self.processed_commands_max // 2]
            for cmd_id in to_remove:
                self.processed_commands.discard(cmd_id)

    def _process_command(self, command_data: Dict[str, Any], source: str = "http"):
        """Process received command"""
        self.stats["commands_received"] += 1

        # Extract command info
        command_id = command_data.get("command_id", "")
        command_type = command_data.get("command_type", command_data.get("command", ""))
        payload = command_data.get("payload", {})

        # Check for duplicate
        if command_id and self._is_duplicate(command_id):
            logger.debug(f"Duplicate command ignored: {command_id}")
            self.stats["duplicates_ignored"] += 1
            return

        # Validate command type
        if command_type not in self.VALID_COMMANDS:
            logger.warning(f"Unknown command type: {command_type}")
            self._send_ack(command_id, "rejected", f"Unknown command: {command_type}")
            return

        # Check TTL if present
        if "ttl_ms" in command_data and "timestamp" in command_data:
            age_ms = (time.time() * 1000) - command_data["timestamp"]
            if age_ms > command_data["ttl_ms"]:
                logger.warning(f"Command expired: {command_id} (age: {age_ms}ms > ttl: {command_data['ttl_ms']}ms)")
                self._send_ack(command_id, "expired", "Command TTL exceeded")
                return

        logger.info(f"Processing command: {command_type} (id: {command_id}, source: {source})")

        # Execute command
        try:
            self.action_callback({
                "command_id": command_id,
                "command_type": command_type,
                "payload": payload,
                "source": source,
                "node_id": command_data.get("node_id", NODE_ID),
            })

            # Mark as processed
            if command_id:
                self._mark_processed(command_id)

            self.stats["commands_executed"] += 1

            # Send ACK if required
            if command_data.get("require_ack", True):
                self._send_ack(command_id, "success", f"Command {command_type} executed")

        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            self.stats["commands_failed"] += 1
            self._send_ack(command_id, "failed", str(e))

    def _send_ack(self, command_id: str, status: str, message: str = ""):
        """Send command acknowledgment to backend"""
        if not command_id:
            return

        ack_payload = {
            "node_id": NODE_ID,
            "command_id": command_id,
            "status": status,
            "message": message,
            "timestamp": time.time(),
        }

        # Try HTTP ACK
        try:
            response = requests.post(
                self.ack_endpoint,
                json=ack_payload,
                timeout=BACKEND_TIMEOUT
            )
            if response.status_code == 200:
                self.stats["acks_sent"] += 1
                logger.debug(f"ACK sent for command {command_id}: {status}")
                return
        except Exception as e:
            logger.debug(f"HTTP ACK failed: {e}")

        # Try MQTT ACK
        if self.mqtt_client and self.mqtt_connected:
            try:
                ack_topic = f"gridguardian/{NODE_ID}/ack"
                self.mqtt_client.publish(ack_topic, json.dumps(ack_payload), qos=1)
                self.stats["acks_sent"] += 1
                logger.debug(f"MQTT ACK sent for command {command_id}: {status}")
            except Exception as e:
                logger.debug(f"MQTT ACK failed: {e}")

    def _poll_commands(self):
        """Poll backend for commands (HTTP)"""
        while self.running:
            try:
                response = requests.get(
                    self.http_endpoint,
                    timeout=BACKEND_TIMEOUT,
                    params={"node_id": NODE_ID}
                )

                if response.status_code == 200:
                    data = response.json()
                    commands = data.get("commands", data.get("data", []))

                    if isinstance(commands, list):
                        for cmd in commands:
                            self._process_command(cmd, source="http")
                    elif isinstance(commands, dict):
                        self._process_command(commands, source="http")

            except requests.exceptions.Timeout:
                logger.debug("Command poll timeout (backend may be busy)")
            except requests.exceptions.ConnectionError:
                logger.debug("Command poll connection error (backend may be down)")
            except Exception as e:
                logger.debug(f"Command poll error: {e}")

            # Wait before next poll
            time.sleep(COMMAND_POLL_INTERVAL)

    def start(self):
        """Start listening for commands"""
        if self.running:
            return

        self.running = True

        # Start MQTT connection
        if self.mqtt_client:
            try:
                self.mqtt_client.connect(
                    MQTT_BROKER,
                    MQTT_PORT,
                    MQTT_KEEPALIVE
                )
                self.mqtt_client.loop_start()
            except Exception as e:
                logger.warning(f"MQTT connection failed: {e}")

        # Start HTTP polling thread
        self.poll_thread = threading.Thread(target=self._poll_commands, daemon=True)
        self.poll_thread.start()

        logger.info("Command listener started")

    def stop(self):
        """Stop listening for commands"""
        self.running = False

        # Stop MQTT
        if self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception:
                pass

        # Wait for poll thread
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=5)

        logger.info("Command listener stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get listener statistics"""
        return {
            **self.stats,
            "running": self.running,
            "mqtt_connected": self.mqtt_connected,
            "processed_commands_count": len(self.processed_commands),
        }
