# hardware/dwin.py
import time
from config import DWIN_PORT, DWIN_BAUD, VPIN_WA, VPIN_WB, VPIN_IA, VPIN_IB, VPIN_TEMP, VPIN_MODE_ADDR
from utils.serial_reconnect import SerialReconnect

class DWINDisplay:
    """Interface to the DWIN serial display."""

    def __init__(self):
        self.ser = SerialReconnect(
            port=DWIN_PORT,
            baudrate=DWIN_BAUD,
            timeout=0.2,
            write_timeout=0.2,
            name="DWIN"
        )
        self._cache = {}

    # ---------- low level write ----------
    def _write_packet(self, vpin, int_value):
        """Send a 5A A5 packet to set a variable address."""
        packet = (
            bytes([0x5A, 0xA5, 0x05, 0x82]) +
            vpin.to_bytes(2, "big") +
            int_value.to_bytes(2, "big", signed=True)
        )
        self.ser.write(packet)

    # ---------- public API ----------
    def send_value(self, vpin, value):
        """Scale float to int16 and write if changed."""
        iv = int(round(value * 10))
        iv = max(-32768, min(32767, iv))

        if self._cache.get(vpin) == iv:
            return
        self._cache[vpin] = iv
        self._write_packet(vpin, iv)

    def send_mode(self, mode):
        """Send mode (V=0, C=1) to VPIN 0x5000."""
        mode_val = 0 if mode == "V" else 1
        self._write_packet(VPIN_MODE_ADDR, mode_val)

    def switch_page(self, page_id):
        """Change to a given page ID."""
        frame = bytes([
            0x5A, 0xA5, 0x07, 0x82,
            0x00, 0x84, 0x5A, 0x01,
            (page_id >> 8) & 0xFF,
            page_id & 0xFF
        ])
        self.ser.write(frame)
        self.ser.flush()
        time.sleep(0.05)
        self.ser.reset_input_buffer()
        print(f"📄 Switched to page {page_id}")

    def read_vp_5100(self, timeout=2.0):
        """Poll VP5100 (water flow sensor) and return integer value."""
        cmd = bytes([0x5A, 0xA5, 0x03, 0x83, 0x51, 0x00])
        start = time.time()
        buffer = b""

        self.ser.reset_input_buffer()
        while time.time() - start < timeout:
            self.ser.write(cmd)
            t0 = time.time()
            while time.time() - t0 < 0.15:
                if self.ser.in_waiting:
                    buffer += self.ser.read(self.ser.in_waiting)
                    if len(buffer) >= 8:
                        return (buffer[-2] << 8) | buffer[-1]
                time.sleep(0.01)
        return None

    # ---------- scaling (specific to display) ----------
    @staticmethod
    def scale_value(raw, mode, function):
        """Convert raw PAM value to display units."""
        raw = float(raw)
        if mode == "V":
            return raw / 1000.0
        if mode == "C":
            if function == 196:
                return (raw * 0.0016) + 4.0
            # function 195 (or default)
            return min(20.0, max(4.0, (raw * 0.0008) + 12.0))
        return None