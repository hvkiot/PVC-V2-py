# hardware/pam.py
import time
from config import PAM_PORT, PAM_BAUD, PAM_CMD_DELAY
from utils.serial_reconnect import SerialReconnect


class PAMController:
    """Interface to the PAM serial controller."""

    def __init__(self):
        self.ser = SerialReconnect(
            port=PAM_PORT,
            baudrate=PAM_BAUD,
            timeout=0.15,
            write_timeout=0.15,
            name="PAM"
        )
        self._connected_once = False

    def cmd(self, command):
        """Send a command and read until prompt '>'."""
        try:
            self.ser.reset_input_buffer()
            self.ser.write((command + "\r\n").encode())

            # Read until '>' appears, with timeout
            response = b""
            start = time.time()
            while time.time() - start < 0.2:  # max 200ms per command
                if self.ser.in_waiting:
                    chunk = self.ser.read(self.ser.in_waiting)
                    response += chunk
                    if b">" in chunk or b"\n" in response:
                        break
                time.sleep(0.001)  # 1ms yield

            return response.decode(errors="ignore")
        except Exception as e:
            print(f"❌ PAM command error: {e}")
            return ""
    # ---------- response parsers ----------

    @staticmethod
    def extract_number(resp):
        for token in resp.replace(">", "").split():
            try:
                return float(token)
            except ValueError:
                pass
        return None

    @staticmethod
    def extract_mode(resp):
        if "V" in resp:
            return "V"
        if "C" in resp:
            return "C"
        return None

    @staticmethod
    def extract_pam_mode(resp):
        if "STD" in resp:
            return "STD"
        if "EXP" in resp:
            return "EXP"
        return None

    # ---------- high level commands ----------
    def read_function(self):
        resp = self.cmd("FUNCTION")
        return self.extract_number(resp)

    def read_ain_mode(self, channel='A'):
        cmd = "AINA" if channel.upper() == 'A' else "AINB"
        resp = self.cmd(cmd)
        return self.extract_mode(resp)

    def read_wa(self):
        resp = self.cmd("WA")
        return self.extract_number(resp)

    def read_wb(self):
        resp = self.cmd("WB")
        return self.extract_number(resp)

    def read_w(self):
        resp = self.cmd("W")
        return self.extract_number(resp)

    def read_ia(self):
        resp = self.cmd("IA")
        return self.extract_number(resp)

    def read_ib(self):
        resp = self.cmd("IB")
        return self.extract_number(resp)

    def ensure_std_mode(self):
        """Check and force STD mode if needed."""
        resp = self.cmd("MODE")
        mode = self.extract_pam_mode(resp)
        if mode == "EXP":
            self.cmd("MODE STD")
            time.sleep(0.1)
            self.cmd("MODE")   # verify

        if not self._connected_once:
            print("✔ PAM MODE verified as STD")
            self._connected_once = True
