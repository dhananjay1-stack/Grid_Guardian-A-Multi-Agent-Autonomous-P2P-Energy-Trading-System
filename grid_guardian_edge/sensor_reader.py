"""
Grid-Guardian Edge - Sensor Reader
PZEM-004T Energy Meter Integration via Modbus RTU
"""

import logging
import random
import struct
import time
from typing import Optional, Dict, Any

from config import (
    SERIAL_PORT,
    BAUD_RATE,
    SERIAL_TIMEOUT,
    MAX_VOLTAGE,
    MIN_VOLTAGE,
    MAX_CURRENT,
    MAX_POWER,
)

logger = logging.getLogger(__name__)


class SensorReader:
    """
    Read energy data from PZEM-004T sensor via serial/Modbus RTU.

    PZEM-004T provides:
    - Voltage (V)
    - Current (A)
    - Power (W)
    - Energy (kWh)
    - Frequency (Hz) - optional
    - Power Factor - optional
    """

    # PZEM-004T Modbus addresses (0x00-based)
    PZEM_ADDR_VOLTAGE = 0x0000  # 2 bytes, 0.1V resolution
    PZEM_ADDR_CURRENT = 0x0001  # 4 bytes (2 registers), 0.001A resolution
    PZEM_ADDR_POWER = 0x0003    # 4 bytes (2 registers), 0.1W resolution
    PZEM_ADDR_ENERGY = 0x0005   # 4 bytes (2 registers), 1Wh resolution
    PZEM_ADDR_FREQ = 0x0007     # 2 bytes, 0.1Hz resolution
    PZEM_ADDR_PF = 0x0008       # 2 bytes, 0.01 resolution

    PZEM_SLAVE_ADDR = 0x01  # Default PZEM slave address
    PZEM_READ_INPUT = 0x04  # Modbus function code for reading input registers

    def __init__(self):
        self.mock_mode = False
        self.ser = None
        self.last_reading = None
        self.read_errors = 0
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5

        try:
            import serial
            self.ser = serial.Serial(
                port=SERIAL_PORT,
                baudrate=BAUD_RATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=SERIAL_TIMEOUT
            )
            logger.info(f"Serial port opened: {SERIAL_PORT} at {BAUD_RATE} baud")
        except ImportError:
            logger.warning("pyserial not installed. Using mock mode.")
            self.mock_mode = True
        except PermissionError as e:
            logger.warning(f"Permission denied for {SERIAL_PORT}. Using mock mode. Error: {e}")
            self.mock_mode = True
        except Exception as e:
            logger.warning(f"Could not open serial port {SERIAL_PORT}. Using mock mode. Error: {e}")
            self.mock_mode = True

    def _calculate_crc(self, data: bytes) -> bytes:
        """Calculate Modbus CRC16"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return struct.pack('<H', crc)

    def _build_read_request(self, start_addr: int, num_registers: int) -> bytes:
        """Build Modbus RTU read input registers request"""
        request = struct.pack(
            '>BBHH',
            self.PZEM_SLAVE_ADDR,
            self.PZEM_READ_INPUT,
            start_addr,
            num_registers
        )
        return request + self._calculate_crc(request)

    def _parse_response(self, response: bytes, expected_bytes: int) -> Optional[bytes]:
        """Parse and validate Modbus response"""
        if not response or len(response) < 5:
            return None

        # Check slave address and function code
        if response[0] != self.PZEM_SLAVE_ADDR:
            return None

        # Check for error response
        if response[1] & 0x80:
            error_code = response[2]
            logger.warning(f"Modbus error response: {error_code}")
            return None

        # Verify CRC
        data = response[:-2]
        received_crc = response[-2:]
        calculated_crc = self._calculate_crc(data)

        if received_crc != calculated_crc:
            logger.warning("CRC mismatch in Modbus response")
            return None

        # Extract data bytes
        byte_count = response[2]
        if byte_count != expected_bytes:
            return None

        return response[3:3 + byte_count]

    def _read_pzem_data(self) -> Optional[Dict[str, float]]:
        """Read all data from PZEM sensor using Modbus RTU"""
        try:
            # Clear any pending data
            self.ser.reset_input_buffer()

            # Read 10 input registers starting from 0x0000
            # This gets: voltage, current (2), power (2), energy (2), freq, pf, alarm
            request = self._build_read_request(0x0000, 10)
            self.ser.write(request)

            # Wait for response (3 + 2*registers + 2 CRC = 25 bytes)
            time.sleep(0.1)
            response = self.ser.read(25)

            data_bytes = self._parse_response(response, 20)
            if data_bytes is None:
                return None

            # Parse the data
            # Voltage: register 0, 0.1V resolution
            voltage = struct.unpack('>H', data_bytes[0:2])[0] / 10.0

            # Current: registers 1-2, 0.001A resolution (low word first)
            current_low = struct.unpack('>H', data_bytes[2:4])[0]
            current_high = struct.unpack('>H', data_bytes[4:6])[0]
            current = (current_high * 65536 + current_low) / 1000.0

            # Power: registers 3-4, 0.1W resolution (low word first)
            power_low = struct.unpack('>H', data_bytes[6:8])[0]
            power_high = struct.unpack('>H', data_bytes[8:10])[0]
            power = (power_high * 65536 + power_low) / 10.0

            # Energy: registers 5-6, 1Wh resolution (low word first)
            energy_low = struct.unpack('>H', data_bytes[10:12])[0]
            energy_high = struct.unpack('>H', data_bytes[12:14])[0]
            energy = (energy_high * 65536 + energy_low) / 1000.0  # Convert to kWh

            # Frequency: register 7, 0.1Hz resolution
            frequency = struct.unpack('>H', data_bytes[14:16])[0] / 10.0

            # Power Factor: register 8, 0.01 resolution
            power_factor = struct.unpack('>H', data_bytes[16:18])[0] / 100.0

            return {
                "voltage": round(voltage, 2),
                "current": round(current, 3),
                "power": round(power, 2),
                "energy": round(energy, 3),
                "frequency": round(frequency, 1),
                "power_factor": round(power_factor, 2),
            }

        except Exception as e:
            logger.error(f"Error reading PZEM data: {e}")
            return None

    def _get_mock_data(self) -> Dict[str, float]:
        """Generate realistic mock sensor data for testing"""
        base_voltage = 230.0
        base_current = 2.5
        base_power = base_voltage * base_current

        # Add some realistic variation
        voltage = base_voltage + random.uniform(-5.0, 5.0)
        current = max(0.1, base_current + random.uniform(-1.0, 1.0))
        power = voltage * current * random.uniform(0.95, 1.0)  # Account for power factor

        # Simulate cumulative energy
        if self.last_reading:
            # Add energy based on time since last reading and current power
            time_delta = 5.0 / 3600.0  # Assuming 5-second intervals, convert to hours
            energy_delta = (power / 1000.0) * time_delta  # Convert W to kWh
            energy = self.last_reading.get("energy", 50.0) + energy_delta
        else:
            energy = random.uniform(50.0, 100.0)

        return {
            "voltage": round(voltage, 2),
            "current": round(current, 3),
            "power": round(power, 2),
            "energy": round(energy, 3),
            "frequency": round(50.0 + random.uniform(-0.2, 0.2), 1),
            "power_factor": round(random.uniform(0.85, 0.99), 2),
        }

    def _validate_reading(self, data: Dict[str, float]) -> tuple[bool, list[str]]:
        """Validate sensor reading against safety limits"""
        issues = []

        if data["voltage"] > MAX_VOLTAGE:
            issues.append(f"Over-voltage: {data['voltage']}V > {MAX_VOLTAGE}V")
        elif data["voltage"] < MIN_VOLTAGE:
            issues.append(f"Under-voltage: {data['voltage']}V < {MIN_VOLTAGE}V")

        if data["current"] > MAX_CURRENT:
            issues.append(f"Over-current: {data['current']}A > {MAX_CURRENT}A")

        if data["power"] > MAX_POWER:
            issues.append(f"Over-power: {data['power']}W > {MAX_POWER}W")

        return len(issues) == 0, issues

    def read_data(self) -> Optional[Dict[str, Any]]:
        """
        Read sensor data - either from real hardware or mock.

        Returns:
            Dictionary with voltage, current, power, energy, etc.
            None if reading fails.
        """
        try:
            if self.mock_mode:
                data = self._get_mock_data()
                data["source"] = "mock"
            else:
                data = self._read_pzem_data()
                if data is None:
                    self.consecutive_errors += 1
                    self.read_errors += 1
                    logger.warning(f"Sensor read failed (consecutive: {self.consecutive_errors})")

                    # Fall back to mock if too many errors
                    if self.consecutive_errors >= self.max_consecutive_errors:
                        logger.warning("Too many consecutive errors. Returning mock data.")
                        data = self._get_mock_data()
                        data["source"] = "mock_fallback"
                    else:
                        return None
                else:
                    data["source"] = "pzem"
                    self.consecutive_errors = 0

            # Add timestamp
            data["timestamp"] = time.time()

            # Validate reading
            is_valid, issues = self._validate_reading(data)
            data["valid"] = is_valid
            if issues:
                data["issues"] = issues
                for issue in issues:
                    logger.warning(f"Sensor validation: {issue}")

            self.last_reading = data
            return data

        except Exception as e:
            logger.error(f"Error in read_data: {e}")
            self.read_errors += 1
            self.consecutive_errors += 1
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Get sensor reader statistics"""
        return {
            "mock_mode": self.mock_mode,
            "read_errors": self.read_errors,
            "consecutive_errors": self.consecutive_errors,
            "last_reading": self.last_reading,
        }

    def reset_energy(self) -> bool:
        """
        Reset the energy counter on PZEM sensor.
        Note: This requires specific Modbus write command.
        """
        if self.mock_mode:
            if self.last_reading:
                self.last_reading["energy"] = 0.0
            return True

        try:
            # PZEM reset energy command: 0x01, 0x42, CRC
            reset_cmd = bytes([0x01, 0x42])
            reset_cmd += self._calculate_crc(reset_cmd)
            self.ser.write(reset_cmd)
            time.sleep(0.1)
            response = self.ser.read(5)
            logger.info("Energy counter reset command sent")
            return len(response) >= 4
        except Exception as e:
            logger.error(f"Error resetting energy counter: {e}")
            return False

    def close(self):
        """Close serial connection"""
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
                logger.info("Serial port closed")
            except Exception as e:
                logger.error(f"Error closing serial port: {e}")
