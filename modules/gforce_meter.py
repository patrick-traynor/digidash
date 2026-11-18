import math

from PyQt5 import QtWidgets, QtGui, QtCore

from .config import Config


class GForceMeterWidget(QtWidgets.QWidget):
    """Friction-circle gauge with a smoothed live dot and a peak-hold marker."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lat_g = 0.0
        self.lon_g = 0.0
        # Smoothed position for the dot; peak-hold uses raw values
        self._smooth_lat = 0.0
        self._smooth_lon = 0.0
        self.peak_lat = 0.0
        self.peak_lon = 0.0
        self._peak_mag = 0.0
        self.max_g = Config.GFORCE_METER_MAX_G
        self.night_mode = False

    def update_g(self, lat_g, lon_g):
        self.lat_g = lat_g
        self.lon_g = lon_g
        alpha = Config.GFORCE_METER_SMOOTHING
        self._smooth_lat += alpha * (lat_g - self._smooth_lat)
        self._smooth_lon += alpha * (lon_g - self._smooth_lon)
        mag = math.hypot(lat_g, lon_g)
        if mag > self._peak_mag:
            self._peak_mag = mag
            self.peak_lat = lat_g
            self.peak_lon = lon_g
        self.update()

    def reset_peak(self):
        self.peak_lat = 0.0
        self.peak_lon = 0.0
        self._peak_mag = 0.0
        self.update()

    def set_night_mode(self, is_night):
        self.night_mode = is_night
        self.update()

    def _colors(self):
        if self.night_mode:
            return {
                "ring": QtGui.QColor(80, 0, 0),
                "peak": QtGui.QColor(180, 60, 60),
                "text": QtGui.QColor(200, 40, 40),
            }
        return {
            "ring": QtGui.QColor(90, 90, 90),
            "peak": QtGui.QColor(180, 180, 180),
            "text": QtGui.QColor(220, 220, 220),
        }

    def _dot_color(self, mag):
        frac = min(mag / self.max_g, 1.0)
        if frac < 0.5:
            return QtGui.QColor(0, 220, 0)
        if frac < 0.8:
            return QtGui.QColor(230, 200, 0)
        return QtGui.QColor(230, 30, 30)

    def _to_point(self, lat, lon, cx, cy, scale, radius):
        # Forward acceleration is drawn upward; out-of-range values clamp to the ring
        pixel_dist = math.hypot(lat, lon) * scale
        if pixel_dist > radius:
            factor = radius / pixel_dist
            lat, lon = lat * factor, lon * factor
        return QtCore.QPointF(cx + lat * scale, cy - lon * scale)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        colors = self._colors()

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 12
        scale = radius / self.max_g

        # Full-scale and half-scale rings, crosshair
        pen = QtGui.QPen(colors["ring"])
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawEllipse(QtCore.QPointF(cx, cy), radius, radius)
        painter.drawEllipse(QtCore.QPointF(cx, cy), radius / 2, radius / 2)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(QtCore.QPointF(cx - radius, cy), QtCore.QPointF(cx + radius, cy))
        painter.drawLine(QtCore.QPointF(cx, cy - radius), QtCore.QPointF(cx, cy + radius))

        if self._peak_mag > 0.02:
            p = self._to_point(self.peak_lat, self.peak_lon, cx, cy, scale, radius)
            pen = QtGui.QPen(colors["peak"])
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawEllipse(p, 6, 6)

        mag = math.hypot(self._smooth_lat, self._smooth_lon)
        p = self._to_point(self._smooth_lat, self._smooth_lon, cx, cy, scale, radius)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(self._dot_color(mag)))
        painter.drawEllipse(p, 8, 8)

        painter.setPen(colors["text"])
        painter.drawText(
            QtCore.QRectF(cx - radius, cy + radius * 0.55, radius * 2, 20),
            QtCore.Qt.AlignCenter,
            f"{mag:.2f} g",
        )
