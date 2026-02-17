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
        """
        Safely changes PAM Function Mode (195/196) without Power Cycle.
        """
        # 1. Validate Input
        if new_mode not in [195, 196]:
            print(f"❌ Invalid mode: {new_mode}")
            return False

        print(f"🔄 Switching to Mode {new_mode} (NO POWER CYCLE STRATEGY)...")

        try:
            # 2. Safety Check: PIN 15 must be OFF [Source: 40]
            self.ser.reset_input_buffer()
            status = self.get_pin_15_status()
            if status:
                print("❌ SAFETY STOP: PIN 15 is ON. Disable the machine first!")
                return False

            # 3. Send Function Command (Triggers 'Inconsistent Data' / Blinking)
            self.ser.reset_input_buffer()
            print(f"-> Sending: FUNCTION {new_mode}")
            write_status = self.write_function_mode(new_mode)
            if not write_status:
                print("❌ Failed to write function mode")
                return False
            time.sleep(0.5)

            # 4. Critical Save (Writes Defaults to EEPROM) [Source: 70, 106]
            self.ser.reset_input_buffer()
            print("-> Sending: SAVE (Writing to EEPROM...)")
            save_status = self.save_pam_settings()
            if not save_status:
                print("❌ Failed to save PAM settings")
                return False

            # WAIT: The board needs time to rebuild the parameter table
            print("⏳ Waiting for internal memory rebuild (3s)...")
            time.sleep(3.0)

            # 5. Verification Loop (Robust)
            print("🔄 Verifying new mode...")

            for attempt in range(1, 10):  # Increased attempts to 10
                self.ser.reset_input_buffer()  # CRITICAL: Clear old status messages like -4.0

                # Request current function
                response = self.cmd("RX1:FUNCTION")

                # CLEAN & PARSE: Handle "196.0", "196", or garbage
                try:
                    if response:
                        # Extract number using regex (handles "196.0" or "FUNCTION 196")
                        import re
                        match = re.search(r"(\d+(\.\d+)?)", str(response))
                        if match:
                            val_float = float(match.group(1))
                            val_int = int(val_float)  # Converts 196.0 -> 196

                            if val_int == new_mode:
                                print(
                                    f"✅ SUCCESS: Board Confirmed Function {val_int}")
                                return True

                            print(
                                f"   ⚠️ Attempt {attempt}: Read '{val_int}' (Expected {new_mode})...")
                        else:
                            print(
                                f"   ⚠️ Attempt {attempt}: Garbage data '{response}'...")
                    else:
                        print(f"   ⚠️ Attempt {attempt}: No response...")

                except Exception as parse_err:
                    print(f"   ⚠️ Parsing error: {parse_err}")

                # Retry Logic
                time.sleep(1.0)

                # If stuck after 4 tries, re-send SAVE (The "Kick")
                if attempt == 4:
                    print("   -> Re-sending SAVE to nudge the processor...")
                    self.cmd("SAVE")
                    time.sleep(1.0)

            print("❌ TIMEOUT: Verification failed.")
            return False

        except Exception as e:
            print(f"❌ Error during mode switch: {e}")
            return False
