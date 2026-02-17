# hardware/pam.py
import time
from config import PAM_PORT, PAM_BAUD, PAM_CMD_DELAY
from utils.serial_reconnect import SerialReconnect
import re


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
        """Send a command and read until the prompt '>' is received."""
        try:
            # Clear input buffer
            self.ser.reset_input_buffer()

            # Send command
            self.ser.write((command + "\r\n").encode())

            # Read until '>' appears or timeout
            response = b""
            start = time.time()
            timeout = 0.2  # 200ms max per command

            while time.time() - start < timeout:
                if self.ser.in_waiting:
                    chunk = self.ser.read(self.ser.in_waiting)
                    response += chunk
                    # Stop if we see the prompt '>' or a newline (sometimes prompt is after)
                    if b">" in chunk or response.endswith(b">"):
                        break
                time.sleep(0.001)  # Yield to other threads

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

    @staticmethod
    def extract_bool(resp):
        if "ON" in resp:
            return True
        if "OFF" in resp:
            return False
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

    def get_ready_led_status(self):
        """Get READYA status using the existing PAM controller."""
        try:
            # Send command and get response
            response = self.cmd("RX1:READYA")
            # Extract number using regex
            match = re.search(r"READYA\s+(-?\d+)", response, re.IGNORECASE)
            if match:
                status_number = int(match.group(1))
                if status_number == -4:
                    return "ON"
                else:
                    return "OFF"
            else:
                cleaned = ' '.join(response.split())
                return f"Parsing Failed. Raw: {cleaned[:50]}"
        except Exception as e:
            return f"Error: {e}"

    def get_ready_status(self):
        """Decode PAM Status Word properly."""

        try:
            response = self.cmd("RX1:READYA")

            match = re.search(r"READYA\s+(-?\d+)", response, re.IGNORECASE)

            if match:
                val = int(match.group(1))

                # Convert to unsigned 16-bit
                status = val & 0xFFFF

                chA = (status & 16384) > 0   # Bit 14
                chB = (status & 32768) > 0   # Bit 15

                if chA and chB:
                    return "A + B ACTIVE"

                elif chA:
                    return "A ACTIVE"

                elif chB:
                    return "B ACTIVE"

                else:
                    return "ALL OFF"

            else:
                cleaned = ' '.join(response.split())
                return f"Parsing Failed: {cleaned[:50]}"

        except Exception as e:
            return f"Error: {e}"

    def get_pin_15_status(self):
        """
        Returns True if PIN 15 is ON, False if OFF.
        Based on your data: PIN 15 adds 64 to the RC:S value.
        """
        try:
            resp = self.cmd("RC:S")
            val = self.extract_number(resp)

            if val is None:
                return False  # Default to OFF if reading fails
            val = int(val)
            # Check Bit 6 (Binary 64)
            return (val & 64) > 0

        except Exception as e:
            print(f"❌ PAM command error: {e}")
            return False

    def get_pin_6_status(self):
        """
        Returns True if PIN 6 is ON, False if OFF.
        Based on your data: PIN 6 adds 8 to the RC:S value.
        Works even if PIN 15 is OFF.
        """
        try:
            resp = self.cmd("RC:S")
            val = self.extract_number(resp)
            if val is None:
                return False  # Default to OFF if reading fails
            val = int(val)
            # Check Bit 3 (Binary 8)
            return (val & 8) > 0
        except Exception as e:
            print(f"❌ PAM command error: {e}")
            return False

    def get_enabled_b_status(self):
        resp = self.cmd("ENABLE_B")
        return self.extract_bool(resp)

    # ------------ write commands ----------

    def write_function_mode(self, mode):
        """
        Writes the function mode to the PAM.
        """
        self.cmd(f"FUNCTION_MODE {mode}")
        return True

    def save_pam_settings(self):
        """
        Saves the PAM settings to the EEPROM.
        """
        self.cmd("SAVE")
        return True

    def change_pam_function(self, new_mode):
        if new_mode not in [195, 196]:
            print(f"❌ Invalid mode: {new_mode}")
            return False

        print(f"🔄 Switching to Mode {new_mode}...")

        # ---- Safety check with retries ----
        for retry in range(3):
            pin_on = self.get_pin_15_status()
            if not pin_on:
                break
            print(f"⚠️ Pin 15 ON on attempt {retry+1}, retrying in 0.5s...")
            time.sleep(0.5)
        else:
            print("❌ SAFETY STOP: PIN 15 remains ON after 3 attempts.")
            return False
        # ------------------------------------

        try:
            # Send FUNCTION command
            self.ser.reset_input_buffer()
            self.cmd(f"FUNCTION {new_mode}")
            time.sleep(0.5)

            # Save to EEPROM
            self.ser.reset_input_buffer()
            self.cmd("SAVE")
            print("⏳ Waiting for EEPROM write (3s)...")
            time.sleep(3.0)

            # Verification (as you already have)
            for attempt in range(1, 10):
                self.ser.reset_input_buffer()
                resp = self.cmd("RX1:FUNCTION")
                # ... your parsing logic ...
                if verified:
                    return True
                if attempt == 4:
                    self.cmd("SAVE")   # Kick the board again
                    time.sleep(1.0)
            print("❌ Verification failed.")
            return False

        except Exception as e:
            print(f"❌ Error: {e}")
            return False
