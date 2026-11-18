import logging
import threading
import time

import serial

from .config import Config


def parse_pid_response(pid, response):
    if not response:
        return None

    try:
        response = response.strip()

        if pid == "VOLT" and "V" in response:
            return float(response.replace("V", "").strip())

        if not response.startswith("41"):
            return None

        if " " not in response and len(response) >= 6:
            hex_chars = response[2:]
            parts = ["41"] + [hex_chars[i:i + 2] for i in range(0, len(hex_chars) - 1, 2)]
        else:
            parts = response.split(" ")

        if len(parts) < 3:
            return None

        if pid == "RPM" and len(parts) >= 4:
            return ((int(parts[2], 16) * 256) + int(parts[3], 16)) / 4
        elif pid == "SPEED":
            return int(parts[2], 16)
        elif pid in ("COOLANT", "INTAKE"):
            return int(parts[2], 16) - 40
        elif pid in ("LOAD", "THROTTLE"):
            return (int(parts[2], 16) * 100) / 255
    except (ValueError, IndexError):
        return None

    return None


def _leading_int(s):
    """Parse the leading integer of s, ignoring anything after it."""
    s = s.strip()
    i = 1 if s[:1] in "+-" else 0
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    return int(s[:j]) if j > i else None


def _parse_accel_response(response):
    """Parse an ATACL "X,Y,Z" reply into an int tuple, or None."""
    if not response:
        return None
    parts = response.strip().split(",")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


class SerialManager:
    def __init__(self):
        self.port = Config.SERIAL_PORT
        self.baudrate = Config.SERIAL_BAUDRATE
        self.ser = None
        self.lock = threading.Lock()
        self.response_cache = {}
        self.cache_timeout = 0.1
        self.connection_attempts = 0
        self.mems_available = False
        self.accel_offset = {"X": 0, "Y": 0, "Z": 0}
        self.unsupported_pids = set()
        self._pid_fail_counts = {}
        self.connect()

    def connect(self):
        try:
            if self.ser:
                self.ser.close()

            self.ser = serial.Serial(self.port, self.baudrate, timeout=Config.SERIAL_TIMEOUT)
            time.sleep(1)

            if self.initialize_adapter():
                logging.info("Serial connection established")
                self.connection_attempts = 0
                return True
            self.ser.close()
            self.ser = None
            return False
        except Exception as e:
            logging.error(f"Serial connection failed: {e}")
            if self.ser:
                self.ser.close()
                self.ser = None
            return False

    def initialize_adapter(self):
        if not self.ser:
            return False

        try:
            init_commands = [
                (b"ATZ\r", 2.0),    # reset
                (b"ATE0\r", 0.5),   # echo off
                (b"ATL0\r", 0.5),   # linefeeds off
                (b"ATS0\r", 0.5),   # spaces off
                (b"ATSP0\r", 0.5),  # auto protocol
            ]
            for cmd, delay in init_commands:
                self.ser.write(cmd)
                time.sleep(delay)
                self.ser.read_all()

            if self._send_command_raw("ATRV") is None:
                return False

            self.unsupported_pids = set()
            self._pid_fail_counts = {}

            self.mems_available = _parse_accel_response(self._send_command_raw("ATACL")) is not None
            if self.mems_available:
                # Zero out mounting tilt; assumes the car is stationary at connect time
                self.accel_offset = self._calibrate_accel()
                logging.info("MEMS accelerometer detected")
            else:
                self.accel_offset = {"X": 0, "Y": 0, "Z": 0}
                logging.info("No MEMS accelerometer on adapter")

            return True
        except Exception as e:
            logging.error(f"Error initializing OBD adapter: {e}")
            return False

    def _calibrate_accel(self, samples=10, delay=0.05):
        readings = []
        for _ in range(samples):
            raw = _parse_accel_response(self._send_command_raw("ATACL"))
            if raw:
                readings.append(raw)
            time.sleep(delay)

        if not readings:
            return {"X": 0, "Y": 0, "Z": 0}
        n = len(readings)
        return {axis: sum(r[i] for r in readings) / n for i, axis in enumerate("XYZ")}

    def _send_command_raw(self, cmd):
        try:
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\r").encode())

            response = ""
            start_time = time.time()
            timeout = 0.3 if cmd in ("010C", "010D") else 0.5
            # A positive OBD response starts with the request mode + 0x40
            try:
                expected_prefix = f"{int(cmd[:2], 16) + 0x40:02X}"
            except ValueError:
                expected_prefix = None

            while time.time() - start_time < timeout:
                if self.ser.in_waiting > 0:
                    char = self.ser.read(1).decode(errors="ignore")
                    if char == "\r":
                        if response and (cmd.startswith("AT") or
                                         (expected_prefix and response.startswith(expected_prefix))):
                            return response
                        response = ""
                    elif char != "\n":
                        response += char
            return None
        except Exception:
            return None

    def send_command(self, cmd):
        with self.lock:
            if not self.ser or not self.ser.is_open:
                return None

            cache_key = (cmd, time.time() // self.cache_timeout)
            if cache_key in self.response_cache:
                return self.response_cache[cache_key]

            response = self._send_command_raw(cmd)
            if response:
                if len(self.response_cache) > 50:
                    self.response_cache.clear()
                self.response_cache[cache_key] = response
            return response

    def read_pid(self, name):
        if name not in Config.PIDS:
            logging.error(f"Unknown PID: {name}")
            return None
        if name in self.unsupported_pids:
            return None

        response = self.send_command(Config.PIDS[name])
        if response is None:
            self._pid_fail_counts[name] = self._pid_fail_counts.get(name, 0) + 1
            if self._pid_fail_counts[name] >= Config.PID_FAILURE_THRESHOLD:
                self.unsupported_pids.add(name)
                logging.warning(f"PID {name} ({Config.PIDS[name]}) not responding, "
                                f"skipping it until reconnect")
            return None

        self._pid_fail_counts[name] = 0
        return parse_pid_response(name, response)

    def has_mems(self):
        return self.mems_available

    def read_accel(self):
        """Return {"LAT_G", "LON_G"} using the axis mapping and scale in Config."""
        if not self.mems_available:
            return None

        raw = _parse_accel_response(self.send_command("ATACL"))
        if raw is None:
            return None

        inverts = {"X": Config.ACCEL_INVERT_X, "Y": Config.ACCEL_INVERT_Y, "Z": Config.ACCEL_INVERT_Z}
        axes = {
            axis: (value - self.accel_offset[axis]) / Config.ACCEL_SCALE * (-1 if inverts[axis] else 1)
            for axis, value in zip("XYZ", raw)
        }
        return {
            "LAT_G": axes[Config.ACCEL_AXIS_LATERAL],
            "LON_G": axes[Config.ACCEL_AXIS_LONGITUDINAL],
        }

    def read_mems_temp(self):
        """Adapter MEMS die temperature in degrees C (ATTEMP)."""
        if not self.mems_available:
            return None

        response = self.send_command("ATTEMP")
        if not response:
            return None

        raw = _leading_int(response)
        if raw is None:
            logging.warning(f"ATTEMP: could not parse response {response!r}")
            return None

        # MPU-6050 formula (raw/340 + 36.53), adapter reports tenths of a degree
        return (raw + 12412) / 34 / 10.0

    def is_connected(self):
        return bool(self.ser and self.ser.is_open)

    def attempt_reconnect(self):
        if self.connection_attempts < Config.MAX_RECONNECT_ATTEMPTS:
            self.connection_attempts += 1
            logging.warning(f"Reconnecting ({self.connection_attempts}/{Config.MAX_RECONNECT_ATTEMPTS})")
            return self.connect()
        return False

    def close(self):
        try:
            if self.ser:
                self.ser.close()
                self.ser = None
        except Exception as e:
            logging.error(f"Error closing serial connection: {e}")
