# utils/serial_reconnect.py - add connection status check
import serial
import time
import threading


class SerialReconnect:
    def __init__(self, port, baudrate, timeout=0.15, write_timeout=0.15,
                 open_retry_delay=1.0, name="Serial"):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.open_retry_delay = open_retry_delay
        self.name = name
        self.ser = None
        self._lock = threading.Lock()
        self._open()
        self._last_activity = time.time()

    def _open(self):
        """Open the serial port, retry forever."""
        attempts = 0
        while True:
            try:
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    timeout=self.timeout,
                    write_timeout=self.write_timeout
                )
                time.sleep(0.5)
                print(f"✅ {self.name} connected on {self.port}")
                return
            except Exception as e:
                attempts += 1
                if attempts % 10 == 0:  # Only print every 10 attempts
                    print(f"⏳ {self.name} not ready ({e}), waiting...")
                time.sleep(self.open_retry_delay)

    def is_connected(self):
        """Check if port is likely connected."""
        try:
            with self._lock:
                return self.ser is not None and self.ser.is_open
        except:
            return False

    def _reopen(self):
        """Close and reopen the port."""
        with self._lock:
            try:
                if self.ser:
                    self.ser.close()
            except:
                pass
            self._open()

    def write(self, data):
        """Write bytes; reopen on failure."""
        try:
            with self._lock:
                self.ser.write(data)
        except Exception as e:
            print(f"❌ {self.name} write error: {e}")
            self._reopen()
            # After reopen, try once more (could loop, but simple)
            with self._lock:
                self.ser.write(data)

    def read(self, size=1):
        """Read up to size bytes; reopen on failure."""
        try:
            with self._lock:
                return self.ser.read(size)
        except Exception as e:
            print(f"❌ {self.name} read error: {e}")
            self._reopen()
            with self._lock:
                return self.ser.read(size)

    def read_all(self):
        """Read all available bytes; reopen on failure."""
        try:
            with self._lock:
                return self.ser.read(self.ser.in_waiting or 1)
        except Exception as e:
            print(f"❌ {self.name} read_all error: {e}")
            self._reopen()
            with self._lock:
                return self.ser.read(self.ser.in_waiting or 1)

    def reset_input_buffer(self):
        """Clear input buffer; reopen on failure."""
        try:
            with self._lock:
                self.ser.reset_input_buffer()
        except Exception as e:
            print(f"❌ {self.name} reset_input_buffer error: {e}")
            self._reopen()
            with self._lock:
                self.ser.reset_input_buffer()

    def flush(self):
        """Flush output buffer; reopen on failure."""
        try:
            with self._lock:
                self.ser.flush()
        except Exception as e:
            print(f"❌ {self.name} flush error: {e}")
            self._reopen()
            with self._lock:
                self.ser.flush()

    @property
    def in_waiting(self):
        """Return bytes in input buffer; reopen on failure."""
        try:
            with self._lock:
                return self.ser.in_waiting
        except Exception:
            return 0
