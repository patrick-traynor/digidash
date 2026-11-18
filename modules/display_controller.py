import json
import logging
import os
import subprocess

from .config import Config


class DisplayController:
    """Backlight control through sysfs, in percent."""

    def __init__(self):
        self.brightness_file = Config.DISPLAY_BRIGHTNESS_FILE
        self.state_file = Config.DISPLAY_STATE_FILE
        self.current_brightness = 100
        self.max_brightness_value = self._get_max_brightness()

        # The exact percentage is saved because it doesn't round-trip through the hardware scale
        saved = self._load_saved_brightness()
        self.current_brightness = saved if saved is not None else self.get_brightness()
        self.set_brightness(self.current_brightness)

    def _load_saved_brightness(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file) as f:
                    return int(json.load(f).get("brightness"))
        except Exception as e:
            logging.warning(f"Could not load saved display brightness: {e}")
        return None

    def _save_brightness(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({"brightness": self.current_brightness}, f)
        except Exception as e:
            logging.error(f"Failed to save display brightness: {e}")

    def _get_max_brightness(self):
        try:
            with open(Config.DISPLAY_MAX_BRIGHTNESS_FILE) as f:
                return int(f.read().strip())
        except Exception:
            return 255

    def increase_brightness(self):
        return self.set_brightness(min(Config.BRIGHTNESS_MAX, self.current_brightness + Config.BRIGHTNESS_STEP))

    def decrease_brightness(self):
        return self.set_brightness(max(Config.BRIGHTNESS_MIN, self.current_brightness - Config.BRIGHTNESS_STEP))

    def set_brightness(self, percentage):
        percentage = max(Config.BRIGHTNESS_MIN, min(Config.BRIGHTNESS_MAX, percentage))
        value = str(int(percentage / 100.0 * self.max_brightness_value))

        if not os.path.exists(self.brightness_file):
            return self.current_brightness

        try:
            with open(self.brightness_file, "w") as f:
                f.write(value)
        except PermissionError:
            try:
                subprocess.run(["sudo", "tee", self.brightness_file], input=value, text=True,
                               stdout=subprocess.DEVNULL, check=True, timeout=5)
            except Exception as e:
                logging.error(f"Could not set brightness: {e}")
                return self.current_brightness
        except Exception as e:
            logging.error(f"Error writing brightness file: {e}")
            return self.current_brightness

        self.current_brightness = percentage
        self._save_brightness()
        return percentage

    def get_brightness(self):
        try:
            with open(self.brightness_file) as f:
                return int(int(f.read().strip()) / self.max_brightness_value * 100)
        except Exception:
            return self.current_brightness
