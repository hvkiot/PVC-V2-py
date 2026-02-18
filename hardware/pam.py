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

    # --------- verify_writes property ---------

    @property
    def verify_writes(self):
        return getattr(self, '_verify_writes', True)

    @verify_writes.setter
    def verify_writes(self, value):
        self._verify_writes = value

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

    # ----------Hidden commands----------
    def read_status_value(self):
        resp = self.cmd("RX1:READYA")
        return self.extract_number(resp)

    def read_remote_control_status(self):
        resp = self.cmd("RC:S")
        return self.extract_number(resp)

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

    def get_ready_status(self):
        """Decode PAM Status Word properly based on Mode 195 or 196."""
        try:
            # response is now a float/int because of self.extract_number()
            val_raw = self.read_status_value()

            if val_raw is None:
                return "No Data"

            # Convert float to int (e.g., -4.0 -> -4)
            val = int(val_raw)
            mode = self.read_function()

            # --- MODE 196 LOGIC (Standard / Dual Throttle) ---
            if mode == 196:
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

            # --- MODE 195 LOGIC (Directional) ---
            elif mode == 195:
                status = val & 0xFFFF
                chA_ok = (status & 256) > 0   # Bit 8
                chB_ok = (status & 128) > 0   # Bit 7

                if status == 65532:
                    return "ALL OFF"
                if chA_ok and chB_ok:
                    return "A + B ACTIVE"
                elif chB_ok:
                    return "B ACTIVE"
                elif chA_ok:
                    return "A ACTIVE"
                else:
                    return "ALL OFF"
        except Exception as e:
            # This is where your 'got float' error was being caught
            return f"READY Error: {e}"

    def get_pin_15_status(self):
        """
        Returns True if PIN 15 is ON, False if OFF.
        Based on your data: PIN 15 adds 64 to the RC:S value.
        """
        try:
            val = self.read_remote_control_status()

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
            val = self.read_remote_control_status()
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

    def get_current_a_status(self):
        """
        Returns the current value for A channel in 196 mode
        """
        resp = self.cmd("CURRENT:A")
        return self.extract_number(resp)

    def get_current_b_status(self):
        """
        Returns the current value for B channel in 196 mode
        """
        """
        Returns the current value for B channel.
        """
        resp = self.cmd("CURRENT:B")
        return self.extract_number(resp)

    def get_current_status(self):
        """
        Returns the current value for A channel in 195 mode
        """
        resp = self.cmd("CURRENT")
        return self.extract_number(resp)

    # ------------ write commands ----------

    def write_current_a(self, value):
        """
        Writes the current value to the PAM.
        """
        cmd = f"CURRENT:A {value}"
        self.cmd(cmd)
        return True

    def write_current_b(self, value):
        """
        Writes the current value to the PAM.
        """
        cmd = f"CURRENT:B {value}"
        self.cmd(cmd)
        return True

    def write_current(self, value):
        """
        Writes the current value to the PAM.
        """
        cmd = f"CURRENT {value}"
        self.cmd(cmd)
        return True

    def write_ain_mode(self, mode, channel='A'):
        """
        Writes the input mode to the PAM.
        """
        cmd = f"AINA {mode}" if channel.upper() == 'A' else f"AINB {mode}"
        self.cmd(cmd)
        return True

    def write_function_mode(self, mode):
        """
        Writes the function mode to the PAM.
        """
        self.cmd(f"FUNCTION {mode}")
        return True

    def save_pam_settings(self):
        """
        Saves the PAM settings to the EEPROM.
        """
        self.cmd("SAVE")
        return True

    # ---------- change mode ----------

    def change_pam_function(self, new_mode):
        """Change PAM function mode (195 or 196)"""
        if new_mode not in [195, 196]:
            return False

        try:
            # Flush before starting
            self.ser.reset_input_buffer()

            # Send FUNCTION_MODE command
            self.write_function_mode(new_mode)
            time.sleep(0.5)

            # Reset current values for both channels (same for both modes)
            self.cmd("IA 0")
            time.sleep(0.1)
            self.cmd("IB 0")
            time.sleep(0.1)

            # Save settings once
            self.save_pam_settings()
            time.sleep(3.0)  # Wait for EEPROM write

            # Verify
            self.ser.reset_input_buffer()
            resp = self.read_function()

            return resp == float(new_mode)

        except Exception as e:
            print(f"❌ Error in change_pam_function: {e}")
            return False

    def change_pam_ain_mode(self, unit, channel):
        """
        Changes the PAM AIN mode for specified channel
        """
        if unit not in ['V', 'C']:
            return False

        try:
            # Flush before starting
            self.ser.reset_input_buffer()

            # Send AIN mode command
            self.write_ain_mode(unit, channel)
            time.sleep(0.5)

            # Save settings
            self.save_pam_settings()
            time.sleep(3.0)  # Wait for EEPROM write

            # Verify
            self.ser.reset_input_buffer()
            resp = self.read_ain_mode(channel)

            return resp == unit

        except Exception as e:
            print(f"❌ Error in change_pam_ain_mode: {e}")
            return False

    def set_current_value(self, value, channel, mode):
        """
        Set current value for A or B channel.

        Args:
            value: Current value (int, 500-2600)
            channel: 'A' or 'B' 
            mode: '195' or '196'

        Returns:
            True if successful, False otherwise
        """
        try:
            # === VALIDATION ===
            # Validate value
            value = int(value)  # Convert once
            if not (500 <= value <= 2600):
                print(f"❌ Value {value} is out of range (500mA-2600mA)")
                return False

            # Validate channel based on mode
            channel = channel.upper()
            if mode == "195" and channel != 'A':
                print(f"⚠️ Mode 195 ignores channel {channel}, using A")
                channel = 'A'  # Auto-correct instead of failing
            elif mode == "196" and channel not in ['A', 'B']:
                print(f"❌ Invalid channel {channel} for mode 196")
                return False

            print(f"📌 Mode {mode}: setting channel {channel} to {value}mA")

            # === EXECUTION ===
            # Flush before starting
            self.ser.reset_input_buffer()

            # Send command based on mode and channel
            if mode == "195":
                # Mode 195: Single channel command
                success = self.write_current(value)
                verify_func = self.get_current_status

            elif mode == "196":
                # Mode 196: Channel-specific command
                if channel == 'A':
                    success = self.write_current_a(value)
                    verify_func = self.get_current_a_status
                else:
                    success = self.write_current_b(value)
                    verify_func = self.get_current_b_status

            if not success:
                print(f"❌ Failed to set current")
                return False

            # Wait for command to process
            time.sleep(0.5)

            # Save settings (only once)
            self.save_pam_settings()
            time.sleep(3.0)  # Wait for EEPROM write

            # Verify (optional - can be disabled for speed)
            if self._verify_writes:  # Add this as class variable if needed
                resp = verify_func()
                if resp != value:
                    print(
                        f"⚠️ Verification failed: expected {value}, got {resp}")
                    return False

            return True

        except ValueError:
            print(f"❌ Invalid value format: {value}")
            return False
        except Exception as e:
            print(f"❌ Error setting current: {e}")
            return False
