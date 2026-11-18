import logging
import subprocess
import sys

from PyQt5 import QtWidgets, uic, QtCore

from modules.config import Config
from modules.serial_manager import SerialManager
from modules.led_controller import LEDController
from modules.data_logger import DataLogger
from modules.display_controller import DisplayController
from modules.can_sniffer_dialog import CANSnifferDialog
from modules.gforce_meter import GForceMeterWidget
from modules.wifi_manager import WifiManager

logging.basicConfig(
    filename=Config.LOGFILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


class DashboardApp(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.night_mode = False

        try:
            uic.loadUi(Config.UI_FILE, self)
        except Exception as e:
            logging.error(f"Failed to load UI: {e}")
            sys.exit(1)

        self.serial_manager = SerialManager()
        self.led_controller = LEDController()
        self.data_logger = DataLogger()
        self.display_controller = DisplayController()
        self.wifi_manager = WifiManager(self)
        self.data_cache = {}

        self.setup_connections()
        self.setup_gforce_meter()
        self.setup_timers()
        self.init_ui_state()

    def setup_gforce_meter(self):
        """Mount the G-force gauge in the 'gforceMeter' placeholder widget, if present."""
        placeholder = self.findChild(QtWidgets.QWidget, "gforceMeter")
        if placeholder:
            self.gforce_widget = GForceMeterWidget(placeholder)
            self.gforce_widget.setGeometry(placeholder.rect())
            self.gforce_widget.set_night_mode(self.night_mode)
            self.gforce_widget.show()
        else:
            self.gforce_widget = None

    def setup_connections(self):
        try:
            self.logButton.clicked.connect(self.toggle_logging)
            self.shutdownButton.clicked.connect(self.shutdown_pi)
            self.brightnessUp.clicked.connect(self.brightness_up)
            self.brightnessDown.clicked.connect(self.brightness_down)
            self.ledUp.clicked.connect(self.led_brightness_up)
            self.ledDown.clicked.connect(self.led_brightness_down)
            self.modeButton.clicked.connect(self.toggle_mode)

            can_btn = self.findChild(QtWidgets.QPushButton, "canButton")
            if can_btn:
                can_btn.clicked.connect(self.open_can_sniffer)

            wifi_btn = self.findChild(QtWidgets.QPushButton, "wifiButton")
            if wifi_btn:
                wifi_btn.clicked.connect(self.wifi_manager.toggle)
            self.wifi_manager.state_changed.connect(self.update_wifi_indicator)
        except AttributeError as e:
            logging.error(f"Button connection failed: {e}")

    def setup_timers(self):
        self.high_freq_timer = QtCore.QTimer(self)
        self.high_freq_timer.timeout.connect(self.update_critical_data)
        self.high_freq_timer.start(Config.HIGH_FREQ_UPDATE)

        self.low_freq_timer = QtCore.QTimer(self)
        self.low_freq_timer.timeout.connect(self.update_background_data)
        QtCore.QTimer.singleShot(1000, lambda: self.low_freq_timer.start(Config.LOW_FREQ_UPDATE))

    def init_ui_state(self):
        try:
            indicator = self.findChild(QtWidgets.QLabel, "loggingIndicator")
            if indicator:
                indicator.setStyleSheet("color: gray;")
                indicator.setText("")

            self._set_label_text("brightnessLabel", self.display_controller.get_brightness())
            self._set_label_text("ledLabel", int(self.led_controller.brightness * 100))
            self.update_wifi_indicator(self.wifi_manager.state, self.wifi_manager.active_ssid,
                                       self.wifi_manager.address)

            QtCore.QTimer.singleShot(1000, self.led_controller.test_sequence)
            self.apply_theme()
        except Exception as e:
            logging.error(f"Error initializing UI: {e}")

    def _set_label_text(self, name, text):
        label = self.findChild(QtWidgets.QLabel, name)
        if label:
            label.setText(str(text))

    def update_label_fast(self, label_name, value):
        label = self.findChild(QtWidgets.QLabel, label_name)
        if not label:
            return
        if not isinstance(value, (int, float)):
            label.setText("--.-")
        elif label_name == "volt":
            label.setText(f"{value:.1f}")
        elif label_name in ("latG", "lonG"):
            label.setText(f"{value:.2f} g")
        elif label_name == "throttle":
            label.setText(f"{value:.0f}%")
        else:
            label.setText(f"{value:.0f}")

    def update_critical_data(self):
        if not self.serial_manager.is_connected():
            self.serial_manager.attempt_reconnect()
            return

        try:
            rpm = self.serial_manager.read_pid("RPM")
            if rpm is not None:
                self.update_label_fast("rpm", rpm)
                self.led_controller.update_rpm_leds(rpm)
                self.data_cache["RPM"] = rpm

            for pid, label in (("SPEED", "speed"), ("THROTTLE", "throttle")):
                value = self.serial_manager.read_pid(pid)
                if value is not None:
                    self.update_label_fast(label, value)
                    self.data_cache[pid] = value

            if self.serial_manager.has_mems():
                accel = self.serial_manager.read_accel()
                if accel is not None:
                    self.data_cache.update(accel)
                    self.update_label_fast("latG", accel["LAT_G"])
                    self.update_label_fast("lonG", accel["LON_G"])
                    if self.gforce_widget:
                        self.gforce_widget.update_g(accel["LAT_G"], accel["LON_G"])

            if self.data_logger.is_logging():
                self.data_logger.log_data(self.data_cache)
        except Exception as e:
            logging.error(f"Error in critical data update: {e}")

    def update_background_data(self):
        if not self.serial_manager.is_connected():
            return

        try:
            for pid, label in (("COOLANT", "coolant"), ("INTAKE", "intake"), ("VOLT", "volt")):
                value = self.serial_manager.read_pid(pid)
                if value is not None:
                    self.data_cache[pid] = value
                    self.update_label_fast(label, value)

            # Adapter board temperature, not a vehicle sensor
            if self.serial_manager.has_mems():
                adapter_temp = self.serial_manager.read_mems_temp()
                if adapter_temp is not None:
                    self.data_cache["ADAPTER_TEMP"] = adapter_temp
                    self.update_label_fast("adapterTemp", adapter_temp)
        except Exception as e:
            logging.error(f"Error in background data update: {e}")

    def brightness_up(self):
        self._set_label_text("brightnessLabel", self.display_controller.increase_brightness())

    def brightness_down(self):
        self._set_label_text("brightnessLabel", self.display_controller.decrease_brightness())

    def led_brightness_up(self):
        self._set_label_text("ledLabel", self.led_controller.increase_brightness())

    def led_brightness_down(self):
        self._set_label_text("ledLabel", self.led_controller.decrease_brightness())

    def open_can_sniffer(self):
        """Pause OBD polling while the sniffer owns the serial port."""
        try:
            self.high_freq_timer.stop()
            self.low_freq_timer.stop()
            dialog = CANSnifferDialog(self.serial_manager, parent=self, night_mode=self.night_mode)
            dialog.exec_()
        except Exception as e:
            logging.error(f"Error opening CAN sniffer: {e}")
        finally:
            QtCore.QTimer.singleShot(500, self._resume_obd_polling)

    def _resume_obd_polling(self):
        try:
            if not self.serial_manager.is_connected():
                self.serial_manager.connect()
            else:
                self.serial_manager.initialize_adapter()
        except Exception as e:
            logging.error(f"Error re-initializing adapter: {e}")
        finally:
            self.high_freq_timer.start(Config.HIGH_FREQ_UPDATE)
            self.low_freq_timer.start(Config.LOW_FREQ_UPDATE)

    def toggle_logging(self):
        indicator = self.findChild(QtWidgets.QLabel, "loggingIndicator")
        if self.data_logger.toggle_logging():
            if indicator:
                indicator.setText("REC")
                indicator.setStyleSheet("color: red; font-weight: bold;")
            if self.gforce_widget:
                self.gforce_widget.reset_peak()
        elif indicator:
            indicator.setText("")
            indicator.setStyleSheet("color: gray;")

    def update_wifi_indicator(self, state, ssid, address):
        texts = {
            WifiManager.CONNECTED: ssid,
            WifiManager.CONNECTING: "CONNECTING",
            WifiManager.DISCONNECTING: "DISCONNECTING",
            WifiManager.FAILED: "FAILED",
            WifiManager.DISCONNECTED: "OFFLINE",
            WifiManager.UNAVAILABLE: "NO NMCLI",
        }
        idle = state in (WifiManager.DISCONNECTED, WifiManager.UNAVAILABLE)
        if self.night_mode:
            color = "rgb(110, 0, 0)" if idle else "red"
        elif idle:
            color = "gray"
        elif state == WifiManager.CONNECTED:
            color = "rgb(0, 200, 0)"
        elif state == WifiManager.FAILED:
            color = "red"
        else:
            color = "orange"

        for name, text in (("wifiIndicator", texts.get(state, "")), ("wifiAddress", address)):
            label = self.findChild(QtWidgets.QLabel, name)
            if label:
                label.setText(text)
                label.setStyleSheet(f"color: {color};")

    def shutdown_pi(self):
        reply = QtWidgets.QMessageBox.question(
            self, "Shutdown System",
            "Are you sure you want to shut down?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            logging.info("Shutting down")
            self.cleanup()
            subprocess.run(["sudo", "shutdown", "now"])

    def cleanup(self):
        try:
            self.data_logger.close()
            self.serial_manager.close()
            self.led_controller.cleanup()
        except Exception as e:
            logging.error(f"Error during cleanup: {e}")

    def closeEvent(self, event):
        self.cleanup()
        event.accept()

    def toggle_mode(self):
        self.night_mode = not self.night_mode
        self.apply_theme()
        if self.gforce_widget:
            self.gforce_widget.set_night_mode(self.night_mode)
        self.update_wifi_indicator(self.wifi_manager.state, self.wifi_manager.active_ssid,
                                   self.wifi_manager.address)

    def apply_theme(self):
        self.setStyleSheet(Config.NIGHT_STYLE if self.night_mode else Config.DAY_STYLE)


if __name__ == "__main__":
    try:
        app = QtWidgets.QApplication(sys.argv)
        window = DashboardApp()
        window.showFullScreen()
        sys.exit(app.exec_())
    except Exception:
        logging.exception("Fatal error in main loop")
        sys.exit(1)
