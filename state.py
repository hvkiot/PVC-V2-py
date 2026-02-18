# state.py
import threading


class MachineState:
    """Thread-safe container for the machine's current readings."""

    def __init__(self):
        self._data = {
            # Flag to indicate if the machine is in transition between modes
            "IN_TRANSITION": False,
            # Actual data's
            "FUNC": None,
            "WA": None,
            "WB": None,
            "IA": None,
            "IB": None,
            "MODE": None,
            "READY": None,
            "PIN15": None,
            "PIN6": None,
            "ENABLED_B": None,
            "CURRENT_A_STATUS": None,
            "CURRENT_B_STATUS": None,
            "CURRENT_STATUS": None
        }
        self._lock = threading.Lock()

    def update(self, **kwargs):
        """Update one or more fields."""
        with self._lock:
            self._data.update(kwargs)

    def get_all(self):
        """Return a copy of the whole state dictionary."""
        with self._lock:
            return self._data.copy()

    def get(self, key):
        with self._lock:
            return self._data.get(key)

    def __getitem__(self, key):
        """Allow dictionary-style access, e.g. state['KEY']"""
        with self._lock:
            return self._data[key]
# New methods for transition handling

    def set_transition(self, in_transition: bool):
        """Set the transition flag"""
        with self._lock:
            self._data["IN_TRANSITION"] = in_transition

    def is_in_transition(self) -> bool:
        """Check if system is in transition"""
        with self._lock:
            return self._data.get("IN_TRANSITION", False)

    def wait_for_transition(self, timeout: float = 2.0) -> bool:
        """
        Wait for transition to complete.
        Returns True if transition completed, False if timeout.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            if not self.is_in_transition():
                return True
            time.sleep(0.05)
        return False
