# state.py
import threading


class MachineState:
    """Thread-safe container for the machine's current readings."""

    def __init__(self):
        self._data = {
            "FUNC": None,
            "WA": None,
            "WB": None,
            "IA": None,
            "IB": None,
            "MODE": None,
            "READY": None,
            "PIN15": None,
            "PIN6": None,
            "ENABLED_B": None
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
