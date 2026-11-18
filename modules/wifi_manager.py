import logging
import shutil

from PyQt5 import QtCore

from .config import Config


class WifiManager(QtCore.QObject):
    """Toggles Config.WIFI_SSID through nmcli and polls the connection state.

    nmcli runs in QProcess so slow connects don't block the UI.
    Emits state_changed(state, ssid, address); ssid and address are empty
    unless CONNECTED.
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    DISCONNECTING = "disconnecting"
    CONNECTED = "connected"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"

    state_changed = QtCore.pyqtSignal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = self.DISCONNECTED
        self.active_ssid = ""
        self.address = ""
        self.device = None
        self._action = None
        self._status = None

        if shutil.which("nmcli") is None or not Config.WIFI_SSID:
            logging.warning("nmcli not found or DIGIDASH_WIFI_SSID unset; WiFi toggle disabled")
            self.state = self.UNAVAILABLE
            return

        self.status_timer = QtCore.QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(Config.WIFI_STATUS_INTERVAL)
        QtCore.QTimer.singleShot(0, self.refresh_status)

    def is_on_configured_network(self):
        return self.state == self.CONNECTED and self.active_ssid == Config.WIFI_SSID

    def toggle(self):
        if self.state == self.UNAVAILABLE or self._action is not None:
            return
        if self.is_on_configured_network():
            self.disconnect_network()
        else:
            self.connect_network()

    def connect_network(self):
        logging.info(f"WiFi: connecting to {Config.WIFI_SSID}")
        self._set_state(self.CONNECTING, "")
        args = ["-w", str(Config.WIFI_CONNECT_TIMEOUT),
                "device", "wifi", "connect", Config.WIFI_SSID]
        if Config.WIFI_PASSWORD:
            args += ["password", Config.WIFI_PASSWORD]
        self._action = self._run_nmcli(args, self._on_action_done)

    def disconnect_network(self):
        # "device disconnect" also stops NetworkManager from auto-reconnecting
        logging.info(f"WiFi: disconnecting {self.device}")
        self._set_state(self.DISCONNECTING, "")
        self._action = self._run_nmcli(["device", "disconnect", self.device], self._on_action_done)

    def refresh_status(self):
        if self._action is not None or self._status is not None:
            return
        self._status = self._run_nmcli(
            ["-t", "-f", "ACTIVE,DEVICE,SSID", "device", "wifi", "list", "--rescan", "no"],
            self._on_status_done,
        )

    def _run_nmcli(self, args, callback):
        proc = QtCore.QProcess(self)

        def done(ok):
            callback(proc, ok)
            proc.deleteLater()

        def on_error(error):
            if error == QtCore.QProcess.FailedToStart:
                done(False)

        proc.finished.connect(
            lambda code, status: done(status == QtCore.QProcess.NormalExit and code == 0))
        proc.errorOccurred.connect(on_error)
        proc.start("nmcli", args)
        return proc

    def _on_action_done(self, proc, ok):
        self._action = None
        if ok:
            self.refresh_status()
        else:
            err = bytes(proc.readAllStandardError()).decode(errors="replace").strip()
            logging.error(f"WiFi: nmcli failed (exit {proc.exitCode()}): {err}")
            self._set_state(self.FAILED, "")
            self.status_timer.start()

    def _on_status_done(self, proc, ok):
        self._status = None
        if not ok or self._action is not None:
            return

        out = bytes(proc.readAllStandardOutput()).decode(errors="replace")
        for line in out.splitlines():
            # ACTIVE:DEVICE:SSID, only the SSID can contain an escaped ':'
            fields = line.split(":", 2)
            if len(fields) == 3 and fields[0] == "yes":
                self.device = fields[1]
                ssid = fields[2].replace("\\:", ":").replace("\\\\", "\\")
                self._status = self._run_nmcli(
                    ["-g", "IP4.ADDRESS", "device", "show", self.device],
                    lambda proc, ok: self._on_address_done(proc, ok, ssid),
                )
                return
        self._set_state(self.DISCONNECTED, "")

    def _on_address_done(self, proc, ok, ssid):
        self._status = None
        if self._action is not None:
            return
        # e.g. "192.168.1.20/24 | ..."
        out = bytes(proc.readAllStandardOutput()).decode(errors="replace") if ok else ""
        address = out.split("|")[0].strip().split("/")[0]
        self._set_state(self.CONNECTED, ssid, address)

    def _set_state(self, state, ssid, address=""):
        if (state, ssid, address) != (self.state, self.active_ssid, self.address):
            self.state, self.active_ssid, self.address = state, ssid, address
            self.state_changed.emit(state, ssid, address)
