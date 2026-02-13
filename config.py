# config.py
# -------------------------------------------------
# Hardware ports & baud rates
PAM_PORT = "/dev/ttyUSB0"
DWIN_PORT = "/dev/serial0"
PAM_BAUD = 57600
DWIN_BAUD = 115200

# Timing
PAM_CMD_DELAY = 0.0
MAIN_LOOP_DELAY = 0.01
MODE_CHECK_INTERVAL = 3.0

# BLE UUIDs (fixed)
SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0"
CHAR_UUID = "12345678-1234-5678-1234-56789abcdef1"

# BLE advertisement name
BLE_DEVICE_NAME = "26020001"

# DWIN VPIN addresses
VPIN_WA = 0x5500
VPIN_WB = 0x5600
VPIN_IA = 0x5700
VPIN_IB = 0x5800
VPIN_TEMP = 0x5900
VPIN_MODE_ADDR = 0x5000      # used for mode packet
