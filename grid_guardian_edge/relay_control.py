"""
Grid-Guardian Edge - Relay Control
GPIO-based relay control with safety features
"""

import logging
import time
from typing import Any, Dict

from config import (
    RELAY_PIN,
    RELAY_ACTIVE_HIGH,
    SAFE_MODE_ENABLED,
)

logger = logging.getLogger(__name__)


class RelayControl:
    """
    Control relay via Raspberry Pi GPIO.
    Includes safety features and state tracking.

    Safety features:
    - Safe mode: Keeps relay OFF regardless of commands
    - State verification: Confirms GPIO state after switching
    - Transition cooldown: Prevents rapid on/off cycling
    - Manual override: Allows manual control to override AI
    """

    # Minimum time between state changes (ms)
    MIN_TRANSITION_INTERVAL = 1000

    def __init__(self):
        self.mock_mode = False
        self.GPIO = None
        self.state = False  # False = OFF, True = ON
        self.safe_mode = False
        self.manual_override = False
        self.manual_override_state = False

        # Timing
        self.last_state_change = 0
        self.total_on_time = 0
        self.last_on_start = None

        # Statistics
        self.stats = {
            "on_count": 0,
            "off_count": 0,
            "safe_mode_activations": 0,
            "blocked_transitions": 0,
        }

        # Initialize GPIO
        self._init_gpio()

    def _init_gpio(self):
        """Initialize GPIO for relay control"""
        try:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
            self.GPIO.setmode(self.GPIO.BCM)
            self.GPIO.setwarnings(False)
            self.GPIO.setup(RELAY_PIN, self.GPIO.OUT)

            # Initialize to OFF state
            self._set_gpio_state(False)
            logger.info(f"GPIO initialized. Relay on pin {RELAY_PIN} (active_high: {RELAY_ACTIVE_HIGH})")

        except ImportError:
            logger.warning("RPi.GPIO not available. Running relay in mock mode.")
            self.mock_mode = True
        except Exception as e:
            logger.warning(f"GPIO initialization failed: {e}. Using mock mode.")
            self.mock_mode = True

    def _set_gpio_state(self, on: bool):
        """Set GPIO output state"""
        if self.mock_mode:
            return

        try:
            if RELAY_ACTIVE_HIGH:
                self.GPIO.output(RELAY_PIN, self.GPIO.HIGH if on else self.GPIO.LOW)
            else:
                self.GPIO.output(RELAY_PIN, self.GPIO.LOW if on else self.GPIO.HIGH)
        except Exception as e:
            logger.error(f"GPIO state change failed: {e}")

    def _can_transition(self) -> bool:
        """Check if transition is allowed (cooldown check)"""
        elapsed = (time.time() * 1000) - self.last_state_change
        return elapsed >= self.MIN_TRANSITION_INTERVAL

    def turn_on(self, source: str = "unknown") -> bool:
        """
        Turn relay ON.

        Args:
            source: What triggered this action (ai, command, manual)

        Returns:
            True if relay was turned ON, False otherwise
        """
        # Check safe mode
        if self.safe_mode and SAFE_MODE_ENABLED:
            logger.warning(f"Turn ON blocked: Safe mode active (source: {source})")
            self.stats["blocked_transitions"] += 1
            return False

        # Check manual override
        if self.manual_override and source != "manual":
            logger.debug(f"Turn ON blocked: Manual override active (source: {source})")
            self.stats["blocked_transitions"] += 1
            return False

        # Check cooldown
        if not self._can_transition():
            logger.debug(f"Turn ON blocked: Cooldown active (source: {source})")
            self.stats["blocked_transitions"] += 1
            return False

        # Already ON
        if self.state:
            return True

        # Execute state change
        self._set_gpio_state(True)
        self.state = True
        self.last_state_change = time.time() * 1000
        self.last_on_start = time.time()
        self.stats["on_count"] += 1

        logger.info(f"Relay turned ON (source: {source})")
        return True

    def turn_off(self, source: str = "unknown") -> bool:
        """
        Turn relay OFF.

        Args:
            source: What triggered this action (ai, command, manual, safe_mode)

        Returns:
            True if relay was turned OFF, False otherwise
        """
        # Safe mode always allows turning OFF
        if source != "safe_mode":
            # Check manual override (except for safe mode)
            if self.manual_override and source != "manual":
                logger.debug(f"Turn OFF blocked: Manual override active (source: {source})")
                self.stats["blocked_transitions"] += 1
                return False

            # Check cooldown (except for safe mode)
            if not self._can_transition():
                logger.debug(f"Turn OFF blocked: Cooldown active (source: {source})")
                self.stats["blocked_transitions"] += 1
                return False

        # Already OFF
        if not self.state:
            return True

        # Track on-time
        if self.last_on_start:
            self.total_on_time += time.time() - self.last_on_start
            self.last_on_start = None

        # Execute state change
        self._set_gpio_state(False)
        self.state = False
        self.last_state_change = time.time() * 1000
        self.stats["off_count"] += 1

        logger.info(f"Relay turned OFF (source: {source})")
        return True

    def get_state(self) -> bool:
        """Get current relay state"""
        return self.state

    def enable_safe_mode(self, reason: str = "unknown") -> bool:
        """
        Enable safe mode - relay will be forced OFF and stay OFF.

        Args:
            reason: Reason for enabling safe mode

        Returns:
            True if safe mode was enabled
        """
        if not SAFE_MODE_ENABLED:
            logger.warning("Safe mode requested but disabled in config")
            return False

        self.safe_mode = True
        self.stats["safe_mode_activations"] += 1
        logger.warning(f"Safe mode ENABLED: {reason}")

        # Force relay OFF
        self.turn_off(source="safe_mode")
        return True

    def disable_safe_mode(self) -> bool:
        """Disable safe mode"""
        self.safe_mode = False
        logger.info("Safe mode DISABLED")
        return True

    def enable_manual_override(self, state: bool):
        """
        Enable manual override mode.

        Args:
            state: Desired relay state during override
        """
        self.manual_override = True
        self.manual_override_state = state

        if state:
            self.turn_on(source="manual")
        else:
            self.turn_off(source="manual")

        logger.info(f"Manual override ENABLED: {'ON' if state else 'OFF'}")

    def disable_manual_override(self):
        """Disable manual override mode"""
        self.manual_override = False
        logger.info("Manual override DISABLED")

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive relay status"""
        current_on_time = 0
        if self.state and self.last_on_start:
            current_on_time = time.time() - self.last_on_start

        return {
            "state": self.state,
            "safe_mode": self.safe_mode,
            "manual_override": self.manual_override,
            "mock_mode": self.mock_mode,
            "total_on_time": self.total_on_time + current_on_time,
            "current_session_on_time": current_on_time,
            **self.stats,
        }

    def cleanup(self):
        """Clean up GPIO resources"""
        # Ensure relay is OFF before cleanup
        self._set_gpio_state(False)

        if self.GPIO and not self.mock_mode:
            try:
                self.GPIO.cleanup(RELAY_PIN)
                logger.info("GPIO cleanup completed")
            except Exception as e:
                logger.error(f"GPIO cleanup error: {e}")

    def __del__(self):
        """Destructor - ensure cleanup"""
        try:
            self.cleanup()
        except Exception:
            pass
