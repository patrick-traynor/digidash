import os


class Config:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    UI_FILE = os.path.join(BASE_DIR, "digidash.ui")
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    LOGFILE = os.path.join(BASE_DIR, "dash.log")
    DISPLAY_STATE_FILE = os.path.join(BASE_DIR, "display_brightness.json")
    LED_STATE_FILE = os.path.join(BASE_DIR, "led_brightness.json")

    # Serial link to the OBD-II adapter
    SERIAL_PORT = "/dev/serial0"
    SERIAL_BAUDRATE = 115200
    SERIAL_TIMEOUT = 0.5
    MAX_RECONNECT_ATTEMPTS = 3
    # A PID that fails this many times in a row is skipped until the next reconnect
    PID_FAILURE_THRESHOLD = 3

    # Update intervals (ms)
    HIGH_FREQ_UPDATE = 100
    LOW_FREQ_UPDATE = 10000

    # WS2812B shift light
    LED_COUNT = 16
    LED_PIN = 18
    LED_BRIGHTNESS_DEFAULT = 0.15
    LED_BRIGHTNESS_MIN = 0.05
    LED_BRIGHTNESS_MAX = 1.0
    LED_BRIGHTNESS_STEP = 0.1
    FLASH_INTERVAL = 200  # ms, used by the concept renderer
    BLUE_FLASH_RPM_MIN = 5800  # shift point
    RED_FLASH_RPM = 6400  # redline

    # Per-LED RPM thresholds and colours
    RPM_THRESHOLDS = [
        3500, 4000, 4200, 4400,
        4600, 4800, 5000, 5100,
        5200, 5300, 5400, 5500,
        5600, 5700, 5800, 5900,
    ]
    LED_COLORS = [(0, 255, 0)] * 6 + [(255, 255, 0)] * 5 + [(255, 0, 0)] * 5

    # Backlight (official Raspberry Pi 7" touchscreen)
    DISPLAY_BRIGHTNESS_FILE = "/sys/class/backlight/rpi_backlight/brightness"
    DISPLAY_MAX_BRIGHTNESS_FILE = "/sys/class/backlight/rpi_backlight/max_brightness"
    BRIGHTNESS_STEP = 10
    BRIGHTNESS_MIN = 10
    BRIGHTNESS_MAX = 100

    # WiFi toggle via nmcli. Set these in the environment, never in this file.
    WIFI_SSID = os.environ.get("DIGIDASH_WIFI_SSID", "")
    WIFI_PASSWORD = os.environ.get("DIGIDASH_WIFI_PASSWORD", "")  # empty = open network
    WIFI_CONNECT_TIMEOUT = 30  # s
    WIFI_STATUS_INTERVAL = 5000  # ms

    # OBD-II PIDs (ATRV is an adapter command, not a PID)
    PIDS = {
        "RPM": "010C",
        "SPEED": "010D",
        "THROTTLE": "0111",
        "COOLANT": "0105",
        "INTAKE": "010F",
        "VOLT": "ATRV",
    }

    # MEMS accelerometer (Freematics ATACL -> "X,Y,Z").
    # Scale is an estimate from road data; tune it for your adapter.
    ACCEL_SCALE = 1000.0  # raw counts per g
    ACCEL_AXIS_LATERAL = "Y"  # positive = right turn
    ACCEL_AXIS_LONGITUDINAL = "X"  # positive = accelerating
    ACCEL_INVERT_X = True
    ACCEL_INVERT_Y = False
    ACCEL_INVERT_Z = False

    # G-force gauge
    GFORCE_METER_MAX_G = 1.5  # full-scale radius
    GFORCE_METER_SMOOTHING = 0.3  # EMA weight for the live dot, 0-1

    # CAN sniffing (Freematics ATM1/ATM0 sniff mode)
    CAN_SNIFF_PROTOCOL_CMD = "ATSP6"  # 11-bit/500 kbps; try ATSP7 for 29-bit
    CAN_SNIFF_HEADER = "000"
    CAN_SNIFF_MASK = "00000000"  # match every ID (used by can_probe.py)
    # Only frames with this DLC are accepted. None = accept 1-8.
    CAN_SNIFF_FRAME_DLC = 8
    # Header/mask presets. An ID passes when (ID & mask) == (header & mask).
    # The 115200 baud link carries ~300 frames/s, so each preset must keep its
    # slice of the bus below that or the adapter replays stale frames.
    CAN_SNIFF_FILTERS = {
        "0xx": ("000", "700"),
        "1xx": ("100", "700"),
        "200-203": ("200", "7FC"),
        "204-207": ("204", "7FC"),
        "208-20F": ("208", "7F8"),
        "210-21F": ("210", "7F0"),
        "220-23F": ("220", "7E0"),
        "240-27F": ("240", "7C0"),
        "280-2FF": ("280", "780"),
        "3xx": ("300", "700"),
        "4xx": ("400", "700"),
        "5xx": ("500", "700"),
        "6xx": ("600", "700"),
        "7xx": ("700", "700"),
    }
    CAN_SNIFF_SATURATION_FPS = 260
    # SWEEP: one pass through every preset, this many seconds each
    CAN_SNIFF_SWEEP_DWELL_S = 8
    CAN_SNIFF_SWEEP_ORDER = list(CAN_SNIFF_FILTERS)

    LOG_HEADERS = [
        "TIME", "RPM", "SPEED", "THROTTLE", "COOLANT",
        "INTAKE", "VOLT", "LAT_G", "LON_G", "ADAPTER_TEMP",
    ]

    DAY_STYLE = """
    QMainWindow {
        background-color: black;
    }
    QLabel[role="label"] {
        border: 1px solid white;
        background: rgb(40, 40, 40);
        color: white;
    }
    QLabel[role="value"] {
        border: 1px solid white;
        color: white;
    }
    QPushButton {
        color: white;
        background-color: rgb(60,60,60);
    }
    """
    NIGHT_STYLE = """
    QMainWindow {
        background-color: black;
    }
    QLabel[role="label"] {
        border: 1px solid red;
        background: black;
        color: red;
    }
    QLabel[role="value"] {
        border: 1px solid red;
        color: red;
    }
    QPushButton {
        color: red;
        background-color: #000;
    }
    """
