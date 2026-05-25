#!/usr/bin/env python3
"""
Grid-Guardian Edge Runtime
Main entry point for Raspberry Pi edge node

Pipeline:
    sensor_reader → condition_detector → ai_adapter → safety_guard → relay_control → telemetry_client

Supports simulation mode for testing without physical hardware.
"""

import argparse
import logging
import signal
import sys
import time
from typing import Any, Dict

from config import (
    setup_logging,
    print_config,
    validate_config,
    LOG_LEVEL,
    NODE_ID,
    LOOP_INTERVAL,
)

# Initialize logging first
logger = setup_logging()


def import_components(simulation_mode=False):
    """Import components after logging is set up"""
    if simulation_mode:
        from virtual_prosumer import SimulationSensorReader as SensorReader
        logger.info("Using SimulationSensorReader for simulation mode")
    else:
        from sensor_reader import SensorReader
    from telemetry_client import TelemetryClient
    from ai_adapter import AiAdapter
    from relay_control import RelayControl
    from command_listener import CommandListener
    from condition_detector import ConditionDetector
    from safety_guard import SafetyGuard
    return SensorReader, TelemetryClient, AiAdapter, RelayControl, CommandListener, ConditionDetector, SafetyGuard


class EdgeRuntime:
    """
    Main runtime controller for Grid-Guardian edge node.

    Orchestrates the complete edge AI pipeline:
    1. Sensor data collection (sensor_reader)
    2. Operating condition detection (condition_detector)
    3. AI decision with model routing (ai_adapter)
    4. Safety validation (safety_guard)
    5. Relay control (relay_control)
    6. Telemetry transmission (telemetry_client)
    7. Command processing (command_listener)

    Supports simulation mode for testing without hardware.
    """

    VERSION = "2.1.0"

    def __init__(self, simulation_mode=False):
        self.running = False
        self.shutdown_requested = False
        self.simulation_mode = simulation_mode

        # Import components (with simulation mode support)
        (SensorReader, TelemetryClient, AiAdapter, RelayControl,
         CommandListener, ConditionDetector, SafetyGuard) = import_components(simulation_mode)

        # Initialize components
        logger.info("Initializing components...")
        if simulation_mode:
            logger.info("  ** SIMULATION MODE ENABLED **")

        # Sensor reader
        self.sensor = SensorReader()
        logger.info(f"  Sensor reader: {'simulation mode' if simulation_mode else ('mock mode' if self.sensor.mock_mode else 'hardware mode')}")

        # Condition detector (loads routing config for thresholds)
        routing_config_path = "models/model_routing.json"
        self.condition_detector = ConditionDetector(routing_config_path)
        logger.info("  Condition detector: initialized")

        # AI adapter with local model inference
        self.ai = AiAdapter()
        ai_status = "local model" if self.ai.local_model_healthy else "backend fallback"
        logger.info(f"  AI adapter: {ai_status} ({self.ai.runtime_type})")

        # Safety guard
        self.safety_guard = SafetyGuard()
        logger.info(f"  Safety guard: {'enabled' if self.safety_guard.enabled else 'disabled'}")

        # Relay control
        self.relay = RelayControl()
        logger.info(f"  Relay control: {'mock mode' if self.relay.mock_mode else 'hardware mode'}")

        # Telemetry client
        self.telemetry = TelemetryClient()
        logger.info("  Telemetry client: initialized")

        # Command listener with callback
        self.cmd_listener = CommandListener(self._handle_command)
        logger.info("  Command listener: initialized")

        # Runtime statistics
        self.stats = {
            "loops_completed": 0,
            "errors": 0,
            "safety_blocks": 0,
            "start_time": None,
        }

    def _handle_command(self, command: Dict[str, Any]):
        """
        Process incoming command from backend.

        Args:
            command: Command dictionary with 'command_type' and 'payload'
        """
        cmd_type = command.get("command_type", "")
        payload = command.get("payload", {})
        source = command.get("source", "unknown")

        logger.info(f"Processing command: {cmd_type} (source: {source})")

        try:
            if cmd_type == "relay_on":
                self.relay.turn_on(source="command")

            elif cmd_type == "relay_off":
                self.relay.turn_off(source="command")

            elif cmd_type == "safe_mode":
                reason = payload.get("reason", "command")
                self.relay.enable_safe_mode(reason=reason)

            elif cmd_type == "disable_safe_mode":
                self.relay.disable_safe_mode()
                self.safety_guard.reset_stats()

            elif cmd_type == "hold":
                duration = payload.get("duration_ms", 60000)
                logger.info(f"Hold command received for {duration}ms")

            elif cmd_type == "ping":
                logger.info("Ping received - node is alive")
                self.telemetry.send_status("online", {"ping_response": True})

            elif cmd_type == "shutdown":
                logger.warning("Shutdown command received")
                self._request_shutdown()

            elif cmd_type == "heartbeat_check":
                self.telemetry.send_status("heartbeat_ack", {
                    "node_id": NODE_ID,
                    "uptime": time.time() - self.stats["start_time"] if self.stats["start_time"] else 0,
                    "loops": self.stats["loops_completed"],
                    "ai_healthy": self.ai.local_model_healthy or self.ai.ai_healthy,
                })

            elif cmd_type == "execute_trade":
                trade_id = payload.get("trade_id")
                action = payload.get("action", "discharge")
                kwh = payload.get("kwh_bucket", 0)
                logger.info(f"Trade execution: {action} {kwh} kWh (trade: {trade_id})")

                if action == "discharge":
                    self.relay.turn_on(source="trade")
                elif action == "charge":
                    self.relay.turn_off(source="trade")

            elif cmd_type == "discharge":
                kwh = payload.get("kwh_amount", 0)
                logger.info(f"Discharge command: {kwh} kWh")
                self.relay.turn_on(source="command")

            elif cmd_type == "charge":
                kwh = payload.get("kwh_amount", 0)
                logger.info(f"Charge command: {kwh} kWh")
                self.relay.turn_off(source="command")

            elif cmd_type == "settlement_complete":
                trade_id = payload.get("trade_id")
                status = payload.get("status")
                logger.info(f"Settlement complete: {trade_id} ({status})")

            elif cmd_type == "delivery_confirmed":
                trade_id = payload.get("trade_id")
                logger.info(f"Delivery confirmed: {trade_id}")

            elif cmd_type == "post_receipt":
                trade_id = payload.get("trade_id")
                logger.info(f"Receipt request for trade: {trade_id}")

            elif cmd_type == "reload_models":
                logger.info("Reloading AI models...")
                self.ai.reload_models()

            elif cmd_type == "get_status":
                # Send comprehensive status
                self._send_comprehensive_status()

            else:
                logger.warning(f"Unhandled command type: {cmd_type}")

        except Exception as e:
            logger.error(f"Error processing command {cmd_type}: {e}")

    def _request_shutdown(self):
        """Request graceful shutdown"""
        self.shutdown_requested = True

    def _apply_ai_decision(self, decision: Dict[str, Any], sensor_data: Dict[str, Any]):
        """
        Apply AI decision to relay after safety validation.

        Args:
            decision: AI decision dictionary
            sensor_data: Current sensor readings
        """
        condition = decision.get("condition", "normal")

        # Safety guard check
        is_safe, safe_decision = self.safety_guard.check_action(decision, sensor_data, condition)

        if not is_safe:
            logger.warning(f"Safety guard blocked action: {decision.get('decision')} -> HOLD")
            self.stats["safety_blocks"] += 1
            self.telemetry.send_alert(
                "safety",
                f"Action blocked: {safe_decision.get('violations', [])}",
                severity="warning"
            )
            return

        # Apply the (potentially modified) safe decision
        action = safe_decision.get("decision", "HOLD")
        confidence = safe_decision.get("confidence", 0.5)
        action_name = safe_decision.get("action_name", "idle")

        logger.debug(
            f"Applying decision: {action} ({action_name}, "
            f"confidence: {confidence:.2f}, condition: {condition})"
        )

        if action == "ON" and not self.relay.get_state():
            self.relay.turn_on(source="ai")
        elif action == "OFF" and self.relay.get_state():
            self.relay.turn_off(source="ai")
        # HOLD = no action

    def _run_loop_iteration(self):
        """
        Execute one iteration of the main loop.

        Pipeline:
            1. Read sensor data
            2. Detect operating condition
            3. Get AI decision (with model routing)
            4. Validate through safety guard
            5. Apply to relay
            6. Send telemetry
        """
        try:
            # 1. Read sensor data
            data = self.sensor.read_data()
            if not data:
                logger.warning("No sensor data available")
                return

            # Add relay state to data
            data["relay_state"] = self.relay.get_state()

            logger.info(
                f"Sensor: V={data.get('voltage', 0):.1f}V "
                f"I={data.get('current', 0):.2f}A "
                f"P={data.get('power', 0):.1f}W "
                f"E={data.get('energy', 0):.2f}kWh "
                f"Relay={'ON' if data['relay_state'] else 'OFF'}"
            )

            # 2. Detect operating condition
            condition = self.condition_detector.detect_condition(data)
            logger.debug(f"Operating condition: {condition}")

            # Check for fault condition
            if self.condition_detector.is_fault_condition():
                logger.warning(f"Fault condition detected - entering safe mode")
                self.relay.enable_safe_mode(reason=f"fault:{condition}")
                self.telemetry.send_alert("fault", f"Fault condition: {condition}", severity="critical")

            # 3. Get AI decision with condition-based model routing
            decision = self.ai.get_decision(data, condition)
            logger.debug(
                f"AI: {decision.get('decision')} "
                f"({decision.get('action_name')}, "
                f"confidence: {decision.get('confidence', 0):.2f}, "
                f"source: {decision.get('source', 'unknown')}, "
                f"model: {decision.get('model_key', 'n/a')})"
            )

            # 4. Apply AI decision through safety guard (if not in safe mode or manual override)
            if not self.relay.safe_mode and not self.relay.manual_override:
                self._apply_ai_decision(decision, data)

            # 5. Send telemetry to backend
            telemetry_data = {
                **data,
                "condition": condition,
                "ai_decision": decision.get("decision"),
                "ai_confidence": decision.get("confidence"),
                "ai_source": decision.get("source"),
                "model_key": decision.get("model_key"),
            }
            telemetry_sent = self.telemetry.send_telemetry(telemetry_data)
            if not telemetry_sent:
                logger.warning("Telemetry send failed (will retry)")

            self.stats["loops_completed"] += 1

        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            self.stats["errors"] += 1
            # On repeated errors, consider safe mode
            if self.stats["errors"] > 10:
                logger.warning("Too many errors - enabling safe mode")
                self.relay.enable_safe_mode(reason="repeated_errors")

    def _send_comprehensive_status(self):
        """Send comprehensive status to backend"""
        status = {
            "node_id": NODE_ID,
            "version": self.VERSION,
            "uptime": time.time() - self.stats["start_time"] if self.stats["start_time"] else 0,
            "loops_completed": self.stats["loops_completed"],
            "errors": self.stats["errors"],
            "safety_blocks": self.stats["safety_blocks"],
            "sensor": self.sensor.get_stats(),
            "ai": self.ai.get_stats(),
            "condition": self.condition_detector.get_stats(),
            "safety": self.safety_guard.get_stats(),
            "relay": self.relay.get_status(),
            "telemetry": self.telemetry.get_stats(),
            "commands": self.cmd_listener.get_stats(),
        }
        self.telemetry.send_status("detailed_status", status)

    def run(self):
        """Main runtime loop"""
        logger.info("=" * 60)
        logger.info(f"Starting Grid-Guardian Edge Runtime v{self.VERSION}")
        logger.info(f"Node ID: {NODE_ID}")
        if self.simulation_mode:
            logger.info("Mode: SIMULATION (Virtual Prosumer)")
        logger.info("=" * 60)

        # Validate configuration
        warnings = validate_config()
        for warning in warnings:
            logger.warning(f"Config: {warning}")

        # Log component status
        logger.info("Component status:")
        logger.info(f"  AI Model: {self.ai.current_model_key or 'none'} ({self.ai.runtime_type})")
        logger.info(f"  Local inference: {'ready' if self.ai.local_model_healthy else 'unavailable'}")
        logger.info(f"  Safety guard: {'enabled' if self.safety_guard.enabled else 'disabled'}")

        self.running = True
        self.stats["start_time"] = time.time()

        # Start command listener
        self.cmd_listener.start()

        # Send startup status
        self.telemetry.send_status("online", {
            "node_id": NODE_ID,
            "version": self.VERSION,
            "mock_sensor": self.sensor.mock_mode,
            "mock_relay": self.relay.mock_mode,
            "ai_model": self.ai.current_model_key,
            "ai_runtime": self.ai.runtime_type,
            "local_inference": self.ai.local_model_healthy,
        })

        try:
            while self.running and not self.shutdown_requested:
                self._run_loop_iteration()
                time.sleep(LOOP_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            self._shutdown()

    def _shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down Grid-Guardian Edge Runtime...")

        self.running = False

        # Stop command listener
        self.cmd_listener.stop()

        # Send offline status
        self.telemetry.send_status("offline", {
            "reason": "shutdown",
            "uptime": time.time() - self.stats["start_time"] if self.stats["start_time"] else 0,
        })

        # Close telemetry client
        self.telemetry.close()

        # Close sensor
        self.sensor.close()

        # Cleanup relay (ensures OFF state)
        self.relay.cleanup()

        # Print statistics
        logger.info("=" * 60)
        logger.info("Runtime Statistics:")
        logger.info(f"  Version: {self.VERSION}")
        logger.info(f"  Loops completed: {self.stats['loops_completed']}")
        logger.info(f"  Errors: {self.stats['errors']}")
        logger.info(f"  Safety blocks: {self.stats['safety_blocks']}")
        logger.info(f"  Sensor stats: {self.sensor.get_stats()}")
        logger.info(f"  Condition stats: {self.condition_detector.get_stats()}")
        logger.info(f"  AI stats: {self.ai.get_stats()}")
        logger.info(f"  Safety stats: {self.safety_guard.get_stats()}")
        logger.info(f"  Relay stats: {self.relay.get_status()}")
        logger.info(f"  Telemetry stats: {self.telemetry.get_stats()}")
        logger.info(f"  Command stats: {self.cmd_listener.get_stats()}")
        logger.info("=" * 60)
        logger.info("Shutdown complete")


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Signal {signum} received")
    sys.exit(0)


def main():
    """Entry point"""
    parser = argparse.ArgumentParser(description="Grid-Guardian Edge Runtime")
    parser.add_argument("--config", action="store_true", help="Print configuration and exit")
    parser.add_argument("--test", action="store_true", help="Run single iteration test")
    parser.add_argument("--bench", action="store_true", help="Run inference benchmark")
    parser.add_argument("--simulation", "-s", action="store_true",
                        help="Run in simulation mode (virtual prosumer, no hardware)")
    args = parser.parse_args()

    if args.config:
        print_config()
        return

    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Create runtime with simulation mode if requested
    runtime = EdgeRuntime(simulation_mode=args.simulation)

    if args.test:
        logger.info("Running single test iteration...")
        runtime._run_loop_iteration()
        logger.info("Test complete")
    elif args.bench:
        logger.info("Running inference benchmark...")
        import numpy as np
        times = []
        for i in range(100):
            start = time.time()
            obs = np.random.randn(18).astype(np.float32)
            # Simulate inference
            runtime.ai._run_inference(obs, runtime.ai.current_model_key)
            times.append((time.time() - start) * 1000)
        logger.info(f"Inference benchmark (100 runs):")
        logger.info(f"  Mean: {np.mean(times):.2f}ms")
        logger.info(f"  Std: {np.std(times):.2f}ms")
        logger.info(f"  Min: {np.min(times):.2f}ms")
        logger.info(f"  Max: {np.max(times):.2f}ms")
    else:
        runtime.run()


if __name__ == "__main__":
    main()
