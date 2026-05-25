"""
Grid-Guardian Edge Configuration
Centralized configuration with environment variable support
"""

import os
import logging
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not installed, use OS environment variables only

# =============================================================================
#                           HARDWARE SETTINGS
# =============================================================================
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/serial0")
BAUD_RATE = int(os.getenv("BAUD_RATE", "9600"))
SERIAL_TIMEOUT = int(os.getenv("SERIAL_TIMEOUT", "2"))

# GPIO settings for relay control
RELAY_PIN = int(os.getenv("RELAY_PIN", "17"))
RELAY_ACTIVE_HIGH = os.getenv("RELAY_ACTIVE_HIGH", "true").lower() == "true"

# =============================================================================
#                           NODE IDENTITY
# =============================================================================
NODE_ID = os.getenv("NODE_ID", "gg-node-01")
NODE_TYPE = os.getenv("NODE_TYPE", "prosumer")  # prosumer, consumer, producer

# =============================================================================
#                           BACKEND CONNECTION
# =============================================================================
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:3000")
BACKEND_TIMEOUT = int(os.getenv("BACKEND_TIMEOUT", "10"))
BACKEND_RETRY_ATTEMPTS = int(os.getenv("BACKEND_RETRY_ATTEMPTS", "3"))
BACKEND_RETRY_DELAY = float(os.getenv("BACKEND_RETRY_DELAY", "2.0"))

# API Endpoints (relative to BACKEND_URL)
API_TELEMETRY = "/api/telemetry"
API_AI_DECIDE = "/api/ai/infer"
API_COMMANDS = "/api/commands"
API_BLOCKCHAIN_TRADE = "/api/blockchain/trade"

# =============================================================================
#                           MQTT SETTINGS (OPTIONAL)
# =============================================================================
MQTT_ENABLED = os.getenv("MQTT_ENABLED", "true").lower() == "true"
MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

# MQTT Topics
MQTT_TOPIC_TELEMETRY = f"gridguardian/{NODE_ID}/telemetry"
MQTT_TOPIC_COMMANDS = f"gridguardian/{NODE_ID}/commands"
MQTT_TOPIC_STATUS = f"gridguardian/{NODE_ID}/status"
MQTT_TOPIC_ALERTS = f"gridguardian/{NODE_ID}/alerts"

# =============================================================================
#                           RUNTIME SETTINGS
# =============================================================================
LOOP_INTERVAL = float(os.getenv("LOOP_INTERVAL", "5.0"))
COMMAND_POLL_INTERVAL = float(os.getenv("COMMAND_POLL_INTERVAL", "5.0"))
TELEMETRY_BUFFER_SIZE = int(os.getenv("TELEMETRY_BUFFER_SIZE", "100"))

# =============================================================================
#                           SAFETY SETTINGS
# =============================================================================
SAFE_MODE_ENABLED = os.getenv("SAFE_MODE_ENABLED", "true").lower() == "true"
MAX_VOLTAGE = float(os.getenv("MAX_VOLTAGE", "260.0"))
MIN_VOLTAGE = float(os.getenv("MIN_VOLTAGE", "180.0"))
MAX_CURRENT = float(os.getenv("MAX_CURRENT", "30.0"))
MAX_POWER = float(os.getenv("MAX_POWER", "6000.0"))

# =============================================================================
#                           LOGGING
# =============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "/var/log/gridguardian-edge.log")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Configure logging
def setup_logging():
    """Configure logging with file and console handlers"""
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    try:
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except (PermissionError, FileNotFoundError):
        pass  # Skip file logging if not available

    return root_logger

# =============================================================================
#                           VALIDATION
# =============================================================================
def validate_config():
    """Validate configuration and return list of warnings"""
    warnings = []

    if not NODE_ID or NODE_ID == "gg-node-01":
        warnings.append("NODE_ID is using default value. Set a unique NODE_ID for production.")

    if BACKEND_URL == "http://127.0.0.1:3000":
        warnings.append("BACKEND_URL is using default value. Set the actual backend URL.")

    if MQTT_ENABLED and MQTT_BROKER == "127.0.0.1":
        warnings.append("MQTT_BROKER is using default value. Set the actual MQTT broker address.")

    return warnings

# Print config on import (for debugging)
def print_config():
    """Print current configuration"""
    print("=" * 60)
    print("Grid-Guardian Edge Configuration")
    print("=" * 60)
    print(f"NODE_ID:       {NODE_ID}")
    print(f"BACKEND_URL:   {BACKEND_URL}")
    print(f"MQTT_ENABLED:  {MQTT_ENABLED}")
    print(f"MQTT_BROKER:   {MQTT_BROKER}:{MQTT_PORT}")
    print(f"SERIAL_PORT:   {SERIAL_PORT}")
    print(f"RELAY_PIN:     {RELAY_PIN}")
    print(f"LOG_LEVEL:     {LOG_LEVEL}")
    print("=" * 60)
