import json
import logging
import os
import time

from rpi_ws281x import PixelStrip, Color

from .config import Config

LED_FREQ_HZ = 800000
LED_DMA = 10
LED_INVERT = False
LED_CHANNEL = 0


class LEDController:
    """WS2812B shift light."""

    def __init__(self):
        self.current_rpm = 0
        self.LED_COUNT = Config.LED_COUNT
        self.brightness_file = Config.LED_STATE_FILE
        self.brightness = self._load_saved_brightness()

        self.strip = PixelStrip(
            self.LED_COUNT, Config.LED_PIN, LED_FREQ_HZ, LED_DMA,
            LED_INVERT, int(self.brightness * 255), LED_CHANNEL,
        )
        self.strip.begin()

    def _load_saved_brightness(self):
        try:
            if os.path.exists(self.brightness_file):
                with open(self.brightness_file) as f:
                    return float(json.load(f).get("brightness", Config.LED_BRIGHTNESS_DEFAULT))
        except Exception as e:
            logging.warning(f"Could not load saved LED brightness: {e}")
        return Config.LED_BRIGHTNESS_DEFAULT

    def _apply_colors(self, colors):
        for i, (r, g, b) in enumerate(colors[:self.LED_COUNT]):
            self.strip.setPixelColor(i, Color(r, g, b))
        self.strip.show()

    def _bar_color(self, i):
        return Config.LED_COLORS[i % len(Config.LED_COLORS)]

    def update_rpm_leds(self, rpm):
        if rpm is None or abs(rpm - self.current_rpm) < 50:
            return
        self.current_rpm = rpm

        if rpm >= Config.RED_FLASH_RPM:
            colors = [(255, 0, 0)] * self.LED_COUNT
        elif rpm >= Config.BLUE_FLASH_RPM_MIN:
            colors = [(0, 0, 255)] * self.LED_COUNT
        else:
            lit = sum(1 for t in Config.RPM_THRESHOLDS if rpm >= t)
            colors = [self._bar_color(i) if i < lit else (0, 0, 0) for i in range(self.LED_COUNT)]
        self._apply_colors(colors)

    def set_brightness(self, brightness):
        self.brightness = max(Config.LED_BRIGHTNESS_MIN, min(Config.LED_BRIGHTNESS_MAX, brightness))
        self.strip.setBrightness(int(self.brightness * 255))

        # Flash the bar briefly so the new level is visible
        self._apply_colors([self._bar_color(i) for i in range(self.LED_COUNT)])
        time.sleep(0.3)
        self.clear_leds()
        self._save_brightness()

    def increase_brightness(self):
        self.set_brightness(self.brightness + Config.LED_BRIGHTNESS_STEP)
        return int(self.brightness * 100)

    def decrease_brightness(self):
        self.set_brightness(self.brightness - Config.LED_BRIGHTNESS_STEP)
        return int(self.brightness * 100)

    def _save_brightness(self):
        try:
            with open(self.brightness_file, "w") as f:
                json.dump({"brightness": self.brightness}, f)
        except Exception as e:
            logging.error(f"Failed to save LED brightness: {e}")

    def test_sequence(self):
        """Startup sweep."""
        try:
            self.clear_leds()
            time.sleep(0.1)
            for i in range(self.LED_COUNT):
                self.strip.setPixelColor(i, Color(*self._bar_color(i)))
                self.strip.show()
                time.sleep(0.05)
            time.sleep(0.3)
            for i in reversed(range(self.LED_COUNT)):
                self.strip.setPixelColor(i, Color(0, 0, 0))
                self.strip.show()
                time.sleep(0.04)
        except Exception as e:
            logging.error(f"LED startup sequence failed: {e}")

    def clear_leds(self):
        self._apply_colors([(0, 0, 0)] * self.LED_COUNT)

    def cleanup(self):
        self.clear_leds()
