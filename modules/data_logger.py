import csv
import logging
import os
import time

from .config import Config


class DataLogger:
    def __init__(self):
        self.logging = False
        self.log_file = None
        self.csv_writer = None
        self.log_filename = None

    def toggle_logging(self):
        """Start or stop CSV logging. Returns True if now logging."""
        try:
            if not self.logging:
                os.makedirs(Config.LOG_DIR, exist_ok=True)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                self.log_filename = os.path.join(Config.LOG_DIR, f"obd_log_{timestamp}.csv")
                self.log_file = open(self.log_filename, "w", newline="")
                self.csv_writer = csv.writer(self.log_file)
                self.csv_writer.writerow(Config.LOG_HEADERS)
                self.log_file.flush()
                self.logging = True
                logging.info(f"Started logging to {self.log_filename}")
            else:
                self.close()
                logging.info(f"Stopped logging to {self.log_filename}")
        except Exception as e:
            logging.error(f"Error in toggle_logging: {e}")
        return self.logging

    def log_data(self, data_cache):
        if not self.logging or not self.csv_writer:
            return

        try:
            now = time.time()
            timestamp = f"{time.strftime('%H:%M:%S', time.localtime(now))}.{int((now % 1) * 1000):03d}"
            row = [timestamp] + [data_cache.get(header, "") for header in Config.LOG_HEADERS[1:]]
            self.csv_writer.writerow(row)
            self.log_file.flush()
        except Exception as e:
            logging.error(f"Error writing to CSV: {e}")

    def is_logging(self):
        return self.logging

    def close(self):
        try:
            if self.log_file:
                self.log_file.close()
        except Exception as e:
            logging.error(f"Error closing log file: {e}")
        finally:
            self.log_file = None
            self.csv_writer = None
            self.logging = False
