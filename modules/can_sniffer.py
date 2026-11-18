"""CAN bus sniffer for the Freematics OBD-II UART adapter.

Uses the adapter's ATM1/ATM0 sniff mode (not ELM327 ATMA). ATM1 streams
space-separated hex tokens with no frame delimiter. Each frame is:

    00 <id_hi> <id_lo> <dlc> <data x dlc> 00

Frames are parsed in a background thread, emitted as Qt signals, and
streamed to logs/can_frames_<time>_<filter>.csv while sniffing.
"""

import csv
import os
import threading
import time
import logging
from collections import defaultdict, deque
from PyQt5 import QtCore
from .config import Config

# Add your vehicle's IDs here as you identify them
KNOWN_IDS = {
    0x7DF: "OBD-II Broadcast Request",
    0x7E8: "ECU Response 1",
    0x7E9: "ECU Response 2",
    0x7EA: "ECU Response 3",
    0x7EB: "ECU Response 4",
}


def decode_frame(can_id_int, data_bytes):
    """Decode an OBD-II PID response frame, or return None."""
    if not data_bytes:
        return None

    if can_id_int in range(0x7E8, 0x7F0) and len(data_bytes) >= 3:
        if data_bytes[0] == 0x41:
            pid = data_bytes[1]
            if pid == 0x0C and len(data_bytes) >= 4:
                rpm = ((data_bytes[2] * 256) + data_bytes[3]) / 4
                return f"RPM: {rpm:.0f}"
            elif pid == 0x0D:
                return f"Speed: {data_bytes[2]} km/h"
            elif pid == 0x05:
                return f"Coolant: {data_bytes[2] - 40}°C"
            elif pid == 0x0F:
                return f"Intake Temp: {data_bytes[2] - 40}°C"
            elif pid == 0x11:
                throttle = (data_bytes[2] * 100) / 255
                return f"Throttle: {throttle:.1f}%"
            elif pid == 0x04:
                load = (data_bytes[2] * 100) / 255
                return f"Engine Load: {load:.1f}%"
            elif pid == 0x0E:
                advance = (data_bytes[2] / 2) - 64
                return f"Timing Advance: {advance:.1f}°"
            elif pid == 0x10:
                maf = ((data_bytes[2] * 256) + data_bytes[3]) / 100
                return f"MAF: {maf:.2f} g/s"
            elif pid == 0x0B:
                return f"MAP: {data_bytes[2]} kPa"
            elif pid == 0x33:
                return f"Baro: {data_bytes[2]} kPa"
            else:
                return f"OBD PID 0x{pid:02X}"

    return None


class CANFrame:
    """Represents a single captured CAN frame."""
    __slots__ = ("can_id", "can_id_int", "raw_bytes", "timestamp",
                 "decoded", "label")

    def __init__(self, can_id_str, raw_bytes, timestamp):
        self.can_id = can_id_str.upper().strip()
        self.raw_bytes = raw_bytes          # list of ints
        self.timestamp = timestamp
        try:
            self.can_id_int = int(self.can_id, 16)
        except ValueError:
            self.can_id_int = 0
        self.label = KNOWN_IDS.get(self.can_id_int, "Unknown")
        self.decoded = decode_frame(self.can_id_int, self.raw_bytes)

    @property
    def data_hex(self):
        return " ".join(f"{b:02X}" for b in self.raw_bytes)

    @property
    def length(self):
        return len(self.raw_bytes)


NOISE_TOKENS = {">", "SEARCHING...", "BUS", "INIT...", "NO", "DATA", "ERROR", "STOPPED", "?"}


class CANSniffer(QtCore.QObject):
    frame_received = QtCore.pyqtSignal(object)  # CANFrame
    status_changed = QtCore.pyqtSignal(str)
    # {can_id: {"count", "hz", "last_frame"}}
    summary_updated = QtCore.pyqtSignal(dict)
    raw_line_received = QtCore.pyqtSignal(str)
    # Sweep done; the receiver must still call stop() to restore the adapter
    sweep_finished = QtCore.pyqtSignal()

    SWEEP = "SWEEP"

    def __init__(self, serial_manager, parent=None):
        super().__init__(parent)
        self._serial_manager = serial_manager
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

        self._id_counts = defaultdict(int)
        self._id_timestamps = defaultdict(lambda: deque(maxlen=20))
        self._id_last_frame = {}
        self._skipped_tokens = 0

        self.filter_name = self.SWEEP
        self._sweep_names = []
        self.active_filter = None
        self._sweep_done = False

        # Opened by start(), written and closed by the sniff thread
        self.frame_log_path = None
        self._frame_log = None
        self._frame_log_writer = None

        self._summary_timer = QtCore.QTimer(self)
        self._summary_timer.timeout.connect(self._emit_summary)

    def start(self, filter_name=SWEEP):
        """Start sniffing with a Config.CAN_SNIFF_FILTERS preset, or SWEEP for all of them."""
        if self._running:
            return
        if filter_name == self.SWEEP:
            names = list(Config.CAN_SNIFF_SWEEP_ORDER)
        else:
            if filter_name not in Config.CAN_SNIFF_FILTERS:
                raise KeyError(filter_name)
            names = [filter_name]
        self.filter_name = filter_name
        self._sweep_names = names
        self.active_filter = names[0]
        self._sweep_done = False
        self._skipped_tokens = 0
        with self._lock:
            self._running = True
            self._id_counts.clear()
            self._id_timestamps.clear()
            self._id_last_frame.clear()

        self._open_frame_log()
        self._thread = threading.Thread(target=self._sniff_loop, daemon=True)
        self._thread.start()
        self._summary_timer.start(500)
        self.status_changed.emit("Sniffing...")
        logging.info("CAN sniffer started")

    def stop(self):
        """Stop sniffing and restore the adapter for normal OBD use."""
        with self._lock:
            self._running = False
        self._summary_timer.stop()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._restore_adapter()
        self.status_changed.emit("Stopped")
        logging.info("CAN sniffer stopped")

    def is_running(self):
        return self._running

    def _sniff_loop(self):
        ser = self._serial_manager.ser
        if not ser or not ser.is_open:
            self.status_changed.emit("No serial connection")
            self._running = False
            self._close_frame_log()
            return

        try:
            sweeping = self.filter_name == self.SWEEP
            step = 0
            self._begin_filter(ser, self._sweep_names[step])
            self._announce_filter(step)
            step_start = time.monotonic()

            char_buffer = ""  # partial token from the last read
            token_queue = []  # complete tokens not yet part of a frame

            # Monotonic, since the Pi has no RTC and wall time can jump
            start_mono = time.monotonic()
            last_flush = start_mono

            while self._running:
                if sweeping and time.monotonic() - step_start >= Config.CAN_SNIFF_SWEEP_DWELL_S:
                    step += 1
                    if step >= len(self._sweep_names):
                        self._sweep_done = True
                        break
                    self._begin_filter(ser, self._sweep_names[step], restart=True)
                    self._announce_filter(step)
                    char_buffer = ""
                    token_queue = []
                    step_start = time.monotonic()
                    continue

                if ser.in_waiting:
                    char_buffer += ser.read(ser.in_waiting).decode(errors="ignore")

                    split_at = max(char_buffer.rfind(" "), char_buffer.rfind("\r"), char_buffer.rfind("\n"))
                    if split_at != -1:
                        complete, char_buffer = char_buffer[:split_at], char_buffer[split_at + 1:]
                        token_queue.extend(t for t in complete.split() if t not in NOISE_TOKENS)

                        frames, token_queue = self._consume_frames(token_queue)
                        elapsed = time.monotonic() - start_mono
                        for frame in frames:
                            self._record_frame(frame)
                            self._log_frame(frame, elapsed)
                            self.frame_received.emit(frame)
                            self.raw_line_received.emit(f"{frame.can_id} {frame.data_hex}")

                        # Flush about once a second to limit SD card writes
                        if self._frame_log and time.monotonic() - last_flush >= 1.0:
                            try:
                                self._frame_log.flush()
                            except Exception as e:
                                logging.error(f"CAN frame log flush failed, closing it: {e}")
                                self._close_frame_log()
                            last_flush = time.monotonic()
                else:
                    time.sleep(0.005)

        except Exception as e:
            logging.error(f"CAN sniffer error: {e}")
            self.status_changed.emit(f"Error: {e}")
        finally:
            self._running = False
            self._close_frame_log()
            logging.info(f"CAN sniffer: {self._skipped_tokens} tokens skipped while framing")
            if self._sweep_done:
                self.sweep_finished.emit()

    def _begin_filter(self, ser, name, restart=False):
        """Apply a filter preset and start ATM1. restart=True stops the running sniff first."""
        header, mask = Config.CAN_SNIFF_FILTERS[name]
        if restart:
            ser.write(b"ATM0\r")
            time.sleep(0.3)
            ser.reset_input_buffer()
            ser.write(b"ATSP0\r")
            time.sleep(0.3)
            ser.read_all()

        ser.write(b"\r")
        time.sleep(0.2)
        ser.reset_input_buffer()

        # ATM1 needs an explicit protocol plus a header filter and mask
        setup_commands = [
            (Config.CAN_SNIFF_PROTOCOL_CMD.encode() + b"\r", 0.3),
            (b"ATCF " + header.encode() + b"\r", 0.3),
            (b"ATCM " + mask.encode() + b"\r", 0.3),
        ]
        for cmd, delay in setup_commands:
            ser.write(cmd)
            time.sleep(delay)
            ser.read_all()

        ser.write(b"ATM1\r")
        time.sleep(0.2)
        self.active_filter = name

    def _announce_filter(self, step):
        if self.filter_name == self.SWEEP:
            self.status_changed.emit(f"Sweep {self.active_filter} ({step + 1}/{len(self._sweep_names)})")
        else:
            self.status_changed.emit("Monitoring bus...")

    def _consume_frames(self, tokens):
        """Parse complete frames from the front of tokens. Returns (frames, leftover).

        A candidate is accepted only if pad and trailer are 00, the ID is a
        nonzero 11-bit value, the DLC matches Config.CAN_SNIFF_FRAME_DLC, and
        the next token (if any) is a 00 pad. Otherwise one token is dropped
        and parsing resyncs.
        """
        frames = []
        i = 0
        n = len(tokens)
        want_dlc = Config.CAN_SNIFF_FRAME_DLC
        while i + 4 <= n:
            try:
                pad = int(tokens[i], 16)
                id_hi = int(tokens[i + 1], 16)
                id_lo = int(tokens[i + 2], 16)
                dlc = int(tokens[i + 3], 16)
            except ValueError:
                self._skipped_tokens += 1
                i += 1
                continue
            can_id_int = (id_hi << 8) | id_lo
            if (pad != 0 or id_hi > 7 or can_id_int == 0
                    or not 1 <= dlc <= 8 or (want_dlc is not None and dlc != want_dlc)):
                self._skipped_tokens += 1
                i += 1
                continue
            frame_len = 4 + dlc + 1
            if i + frame_len > n:
                break
            try:
                data = [int(b, 16) for b in tokens[i + 4:i + 4 + dlc]]
                trailer = int(tokens[i + 4 + dlc], 16)
                next_pad = int(tokens[i + frame_len], 16) if i + frame_len < n else 0
            except ValueError:
                self._skipped_tokens += 1
                i += 1
                continue
            if trailer != 0 or next_pad != 0:
                self._skipped_tokens += 1
                i += 1
                continue
            frames.append(CANFrame(f"{can_id_int:03X}", data, time.time()))
            i += frame_len
        return frames, tokens[i:]

    def _record_frame(self, frame):
        cid = frame.can_id
        self._id_counts[cid] += 1
        self._id_timestamps[cid].append(frame.timestamp)
        self._id_last_frame[cid] = frame

    def _open_frame_log(self):
        try:
            os.makedirs(Config.LOG_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            self.frame_log_path = os.path.join(Config.LOG_DIR,f"can_frames_{ts}_{self.filter_name}.csv")
            self._frame_log = open(self.frame_log_path, "w", newline="")
            self._frame_log_writer = csv.writer(self._frame_log)
            self._frame_log_writer.writerow(["ELAPSED_S", "TIME", "CAN_ID", "DLC", "DATA", "FILTER"])
            self._frame_log.flush()
            logging.info(f"CAN frame log: {self.frame_log_path}")
        except Exception as e:
            logging.error(f"Could not open CAN frame log: {e}")
            self.frame_log_path = None
            self._frame_log = None
            self._frame_log_writer = None

    def _log_frame(self, frame, elapsed):
        if not self._frame_log_writer:
            return
        wall = f"{time.strftime('%H:%M:%S', time.localtime(frame.timestamp))}.{int((frame.timestamp % 1) * 1000):03d}"
        try:
            self._frame_log_writer.writerow([f"{elapsed:.3f}", wall, frame.can_id, frame.length, frame.data_hex, self.active_filter])
        except Exception as e:
            logging.error(f"CAN frame log write failed, closing it: {e}")
            self._close_frame_log()

    def _close_frame_log(self):
        try:
            if self._frame_log:
                self._frame_log.close()
        except Exception as e:
            logging.error(f"Error closing CAN frame log: {e}")
        finally:
            self._frame_log = None
            self._frame_log_writer = None

    def _emit_summary(self):
        summary = {}
        for cid, count in self._id_counts.items():
            timestamps = self._id_timestamps[cid]
            if len(timestamps) >= 2:
                span = timestamps[-1] - timestamps[0]
                hz = (len(timestamps) - 1) / span if span > 0 else 0.0
            else:
                hz = 0.0
            summary[cid] = {
                "count": count,
                "hz": hz,
                "last_frame": self._id_last_frame.get(cid),
            }
        self.summary_updated.emit(summary)

    def _restore_adapter(self):
        try:
            ser = self._serial_manager.ser
            if not ser or not ser.is_open:
                return
            ser.write(b"ATM0\r")
            time.sleep(0.3)
            ser.reset_input_buffer()
            ser.write(b"ATSP0\r")
            time.sleep(0.3)
            ser.read_all()
            logging.info("Serial adapter restored for OBD polling")
        except Exception as e:
            logging.error(f"Error restoring adapter: {e}")
