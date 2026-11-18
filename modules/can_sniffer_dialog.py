import time
import csv
import logging
import os
from PyQt5 import QtWidgets, QtCore, QtGui
from .can_sniffer import CANSniffer
from .config import Config


class IDTableItem(QtWidgets.QTableWidgetItem):
    """Sortable table item that stores a numeric value for sorting."""
    def __init__(self, display_text, sort_value=None):
        super().__init__(display_text)
        self._sort_value = sort_value if sort_value is not None else display_text

    def __lt__(self, other):
        try:
            return float(self._sort_value) < float(other._sort_value)
        except (ValueError, TypeError):
            return str(self._sort_value) < str(other._sort_value)


class CANSnifferDialog(QtWidgets.QDialog):
    """800x480 CAN bus monitor for the 7" touchscreen."""

    def __init__(self, serial_manager, parent=None, night_mode=False):
        super().__init__(parent)
        self.serial_manager = serial_manager
        self.night_mode = night_mode
        self.sniffer = CANSniffer(serial_manager, self)
        self.total_frames = 0
        self._sniffing = False
        self.selected_id = None
        self._recent_frames = {}  # can_id -> list of CANFrame
        self._MAX_RECENT = 50
        self._MAX_TABLE_ROWS = 200

        self.setWindowTitle("CAN Bus Monitor")
        self.setFixedSize(800, 480)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)

        self._build_ui()
        self._apply_theme()
        self._connect_signals()


    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)

        title = QtWidgets.QLabel("CAN Bus Monitor")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        self.statusLabel = QtWidgets.QLabel("Idle")
        self.statusLabel.setMinimumWidth(160)
        header.addWidget(self.statusLabel)

        self.frameCountLabel = QtWidgets.QLabel("Frames: 0")
        header.addWidget(self.frameCountLabel)

        self.idCountLabel = QtWidgets.QLabel("IDs: 0")
        header.addWidget(self.idCountLabel)

        self.sniffButton = QtWidgets.QPushButton("▶ SNIFF")
        self.sniffButton.setFixedWidth(90)
        self.sniffButton.clicked.connect(self.toggle_sniff)
        header.addWidget(self.sniffButton)

        self.exportButton = QtWidgets.QPushButton("EXPORT")
        self.exportButton.setFixedWidth(80)
        self.exportButton.clicked.connect(self.export_csv)
        header.addWidget(self.exportButton)

        closeBtn = QtWidgets.QPushButton("✕ CLOSE")
        closeBtn.setFixedWidth(80)
        closeBtn.clicked.connect(self.close)
        header.addWidget(closeBtn)

        root.addLayout(header)

        options = QtWidgets.QHBoxLayout()
        options.setSpacing(6)

        options.addWidget(QtWidgets.QLabel("Filter:"))
        self.filterCombo = QtWidgets.QComboBox()
        self.filterCombo.addItems([CANSniffer.SWEEP] + list(Config.CAN_SNIFF_FILTERS))
        self.filterCombo.setFixedWidth(110)
        options.addWidget(self.filterCombo)

        self.frameLogLabel = QtWidgets.QLabel("Frame log: starts with SNIFF")
        self.frameLogLabel.setStyleSheet("font-size: 11px;")
        options.addWidget(self.frameLogLabel)
        options.addStretch()

        # A saturated link replays stale frames, so show the live rate
        self.rateLabel = QtWidgets.QLabel("Rate: —")
        self.rateLabel.setStyleSheet("font-size: 11px;")
        options.addWidget(self.rateLabel)
        self._rate_mark = None  # (monotonic time, total_frames)

        root.addLayout(options)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        root.addWidget(line)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(6)

        self.idTable = QtWidgets.QTableWidget()
        self.idTable.setColumnCount(4)
        self.idTable.setHorizontalHeaderLabels(["CAN ID", "Label", "Hz", "Count"])
        self.idTable.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.idTable.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.idTable.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.idTable.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.idTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.idTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.idTable.verticalHeader().setVisible(False)
        self.idTable.setSortingEnabled(True)
        self.idTable.setMinimumWidth(340)
        self.idTable.currentCellChanged.connect(self._on_table_selection)
        body.addWidget(self.idTable, stretch=4)

        detail_layout = QtWidgets.QVBoxLayout()
        detail_layout.setSpacing(4)

        detail_header = QtWidgets.QLabel("Raw output (tap an ID above for details)")
        detail_header.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.detailHeader = detail_header
        detail_layout.addWidget(detail_header)

        self.detailLabel = QtWidgets.QLabel()
        self.detailLabel.setWordWrap(True)
        self.detailLabel.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.detailLabel.setStyleSheet("font-size: 12px;")
        detail_layout.addWidget(self.detailLabel)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        detail_layout.addWidget(sep)

        self.recentLabel = QtWidgets.QLabel("Raw lines from adapter:")
        self.recentLabel.setStyleSheet("font-size: 11px; font-weight: bold;")
        detail_layout.addWidget(self.recentLabel)

        self.recentList = QtWidgets.QListWidget()
        self.recentList.setFont(QtGui.QFont("Courier New", 10))
        self.recentList.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        detail_layout.addWidget(self.recentList, stretch=1)

        detail_widget = QtWidgets.QWidget()
        detail_widget.setLayout(detail_layout)
        body.addWidget(detail_widget, stretch=3)

        root.addLayout(body)

        self._row_for_id = {}


    def _connect_signals(self):
        self.sniffer.frame_received.connect(self._on_frame)
        self.sniffer.status_changed.connect(self._on_status)
        self.sniffer.summary_updated.connect(self._on_summary)
        self.sniffer.raw_line_received.connect(self._on_raw_line)
        self.sniffer.sweep_finished.connect(self._on_sweep_finished)


    def toggle_sniff(self):
        if self._sniffing:
            self._sniffing = False
            self.sniffer.stop()
            self._show_stopped()
        else:
            self._sniffing = True
            self._rate_mark = None
            self.sniffer.start(self.filterCombo.currentText())
            self.sniffButton.setText("■ STOP")
            self.filterCombo.setEnabled(False)
            path = self.sniffer.frame_log_path
            self.frameLogLabel.setText(
                f"Frame log: {os.path.basename(path)}" if path
                else "Frame log: failed to open (see dash.log)"
            )

    def _show_stopped(self):
        self.sniffButton.setText("▶ SNIFF")
        self.filterCombo.setEnabled(True)
        self._set_rate_text(None)

    def _on_sweep_finished(self):
        if not self._sniffing:
            return
        self._sniffing = False
        self.sniffer.stop()
        self._show_stopped()
        self.statusLabel.setText("Sweep complete")

    def _set_rate_text(self, fps):
        if fps is None:
            self.rateLabel.setText("Rate: —")
            self.rateLabel.setStyleSheet("font-size: 11px;")
        elif fps >= Config.CAN_SNIFF_SATURATION_FPS:
            self.rateLabel.setText(f"Rate: {fps:.0f}/s  SATURATED - narrow the filter")
            self.rateLabel.setStyleSheet(
                "font-size: 11px; font-weight: bold;" + ("" if self.night_mode else " color: #ff4444;")
            )
        else:
            self.rateLabel.setText(f"Rate: {fps:.0f}/s")
            self.rateLabel.setStyleSheet("font-size: 11px;")

    def export_csv(self):
        """Export the per-ID summary table to a CSV in the log directory."""
        try:
            os.makedirs(Config.LOG_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(Config.LOG_DIR, f"can_sniff_{ts}.csv")
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["CAN_ID", "Label", "Count", "Hz", "Last_Data_Hex", "Last_Decoded"])
                for row in range(self.idTable.rowCount()):
                    cid = self.idTable.item(row, 0).text() if self.idTable.item(row, 0) else ""
                    label = self.idTable.item(row, 1).text() if self.idTable.item(row, 1) else ""
                    hz = self.idTable.item(row, 2).text() if self.idTable.item(row, 2) else ""
                    count = self.idTable.item(row, 3).text() if self.idTable.item(row, 3) else ""
                    frame = self._recent_frames.get(cid, [None])[-1]
                    data_hex = frame.data_hex if frame else ""
                    decoded = frame.decoded or "" if frame else ""
                    writer.writerow([cid, label, count, hz, data_hex, decoded])
            logging.info(f"CAN sniff exported to {path}")
            msg = f"Saved to:\n{path}"
            if self.sniffer.frame_log_path:
                msg += f"\n\nFull frame log (written live while sniffing):\n{self.sniffer.frame_log_path}"
            QtWidgets.QMessageBox.information(self, "Exported", msg)
        except Exception as e:
            logging.error(f"CAN export error: {e}")
            QtWidgets.QMessageBox.warning(self, "Export Failed", str(e))


    def _on_status(self, msg):
        self.statusLabel.setText(msg)

    def _on_raw_line(self, line):
        """Show the live frame stream while no ID is selected."""
        if self.selected_id is not None:
            return
        ts = time.strftime("%H:%M:%S")
        ms = int((time.time() % 1) * 1000)
        self.recentList.insertItem(0, f"[{ts}.{ms:03d}]  {line}")
        if self.recentList.count() > 200:
            self.recentList.takeItem(self.recentList.count() - 1)

    def _on_frame(self, frame):
        self.total_frames += 1
        cid = frame.can_id
        if cid not in self._recent_frames:
            self._recent_frames[cid] = []
        buf = self._recent_frames[cid]
        buf.append(frame)
        if len(buf) > self._MAX_RECENT:
            buf.pop(0)

        if self.selected_id == cid:
            self._refresh_detail_live(frame)

        self.frameCountLabel.setText(f"Frames: {self.total_frames:,}")

    def _on_summary(self, summary):
        self.idCountLabel.setText(f"IDs: {len(summary)}")

        now = time.monotonic()
        if self._rate_mark is not None and now > self._rate_mark[0]:
            self._set_rate_text((self.total_frames - self._rate_mark[1]) / (now - self._rate_mark[0]))
        self._rate_mark = (now, self.total_frames)

        self.idTable.setSortingEnabled(False)
        self.idTable.blockSignals(True)

        for cid, info in summary.items():
            frame = info["last_frame"]
            hz = info["hz"]
            count = info["count"]
            label = frame.label if frame else "Unknown"
            hz_str = f"{hz:.1f}"
            count_str = str(count)

            if cid in self._row_for_id:
                row = self._row_for_id[cid]
            elif self.idTable.rowCount() < self._MAX_TABLE_ROWS:
                row = self.idTable.rowCount()
                self.idTable.insertRow(row)
                self._row_for_id[cid] = row
            else:
                continue

            self.idTable.setItem(row, 0, IDTableItem(cid, int(cid, 16)))
            self.idTable.setItem(row, 1, IDTableItem(label))
            self.idTable.setItem(row, 2, IDTableItem(hz_str, hz))
            self.idTable.setItem(row, 3, IDTableItem(count_str, count))

            colour = self._hz_colour(hz)
            for col in range(4):
                item = self.idTable.item(row, col)
                if item:
                    item.setForeground(QtGui.QColor(colour))

        self.idTable.blockSignals(False)
        self.idTable.setSortingEnabled(True)

    def _hz_colour(self, hz):
        if self.night_mode:
            if hz > 50:  return "#ff4444"
            if hz > 10:  return "#ff8c00"
            if hz > 1:   return "#cc0000"
            return "#880000"
        else:
            if hz > 50:  return "#00cc44"
            if hz > 10:  return "#ffa500"
            if hz > 1:   return "#ffffff"
            return "#888888"


    def _on_table_selection(self, row, _col, _old_row, _old_col):
        item = self.idTable.item(row, 0)
        if not item:
            return
        self.selected_id = item.text()
        self._refresh_detail_full()

    def _refresh_detail_full(self):
        cid = self.selected_id
        frames = self._recent_frames.get(cid, [])
        if not frames:
            return
        frame = frames[-1]
        self.detailHeader.setText(f"0x{frame.can_id}  —  {frame.label}")
        self.recentLabel.setText("Recent frames:")
        self._update_detail_text(frame, len(frames))
        self.recentList.clear()
        for f in reversed(frames[-20:]):
            ts = time.strftime("%H:%M:%S", time.localtime(f.timestamp))
            ms = int((f.timestamp % 1) * 1000)
            decoded_str = f"  → {f.decoded}" if f.decoded else ""
            self.recentList.addItem(f"[{ts}.{ms:03d}]  {f.data_hex}{decoded_str}")

    def _refresh_detail_live(self, frame):
        self._update_detail_text(frame, len(self._recent_frames.get(frame.can_id, [])))
        ts = time.strftime("%H:%M:%S", time.localtime(frame.timestamp))
        ms = int((frame.timestamp % 1) * 1000)
        decoded_str = f"  → {frame.decoded}" if frame.decoded else ""
        self.recentList.insertItem(0, f"[{ts}.{ms:03d}]  {frame.data_hex}{decoded_str}")
        if self.recentList.count() > 20:
            self.recentList.takeItem(self.recentList.count() - 1)

    def _update_detail_text(self, frame, seen_count):
        lines = [
            f"<b>CAN ID:</b> 0x{frame.can_id}  ({frame.can_id_int})",
            f"<b>Label:</b> {frame.label}",
            f"<b>DLC:</b> {frame.length} bytes",
            f"<b>Data:</b> <code>{frame.data_hex}</code>",
            f"<b>Decoded:</b> {frame.decoded or '—'}",
            f"<b>Frames seen:</b> {seen_count}",
        ]
        self.detailLabel.setText("<br>".join(lines))


    def _apply_theme(self):
        if self.night_mode:
            self.setStyleSheet("""
                QDialog, QWidget { background-color: #0a0000; color: #cc0000; }
                QTableWidget { background-color: #120000; color: #cc0000;
                               gridline-color: #330000; border: 1px solid #440000; }
                QHeaderView::section { background-color: #1a0000; color: #ff4444;
                                       border: 1px solid #330000; }
                QListWidget { background-color: #0a0000; color: #aa0000;
                              font-family: 'Courier New'; border: 1px solid #330000; }
                QPushButton { background-color: #1a0000; color: #cc0000;
                              border: 1px solid #440000; padding: 4px 8px; }
                QPushButton:pressed { background-color: #330000; }
                QComboBox { background-color: #1a0000; color: #cc0000;
                            border: 1px solid #440000; padding: 4px 8px; }
                QComboBox QAbstractItemView { background-color: #120000; color: #cc0000;
                                              selection-background-color: #330000; }
                QPushButton:disabled, QComboBox:disabled { color: #440000; }
                QLabel { color: #cc0000; }
                QFrame { color: #330000; }
            """)
        else:
            self.setStyleSheet("""
                QDialog, QWidget { background-color: #111; color: #eee; }
                QTableWidget { background-color: #1a1a1a; color: #eee;
                               gridline-color: #333; border: 1px solid #444; }
                QHeaderView::section { background-color: #2a2a2a; color: #fff;
                                       border: 1px solid #444; }
                QListWidget { background-color: #111; color: #aaa;
                              font-family: 'Courier New'; border: 1px solid #333; }
                QPushButton { background-color: #2a2a2a; color: #fff;
                              border: 1px solid #555; padding: 4px 8px; }
                QPushButton:pressed { background-color: #444; }
                QComboBox { background-color: #2a2a2a; color: #fff;
                            border: 1px solid #555; padding: 4px 8px; }
                QComboBox QAbstractItemView { background-color: #1a1a1a; color: #eee;
                                              selection-background-color: #444; }
                QPushButton:disabled, QComboBox:disabled { color: #666; }
                QLabel { color: #eee; }
                QFrame { color: #444; }
            """)


    def closeEvent(self, event):
        # A finished SWEEP is no longer running but still needs stop()
        if self._sniffing:
            self._sniffing = False
            self.sniffer.stop()
        event.accept()
