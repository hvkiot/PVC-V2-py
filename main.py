#!/usr/bin/env python3
# main.py
import time
import threading

from config import (
    MAIN_LOOP_DELAY, MODE_CHECK_INTERVAL,
    VPIN_WA, VPIN_WB, VPIN_IA, VPIN_IB, VPIN_TEMP
)
from state import MachineState
from hardware.pam import PAMController
from hardware.dwin import DWINDisplay
from ble.gatt_server import run_ble_server

def main():
    print("--- Initializing system ---")

    # Shared state
    state = MachineState()

    # Hardware interfaces
    pam = PAMController()
    dwin = DWINDisplay()

    # Start BLE server in background thread
    ble_thread = threading.Thread(target=run_ble_server, args=(state,), daemon=True)
    ble_thread.start()

    print("--- System running ---")
    last_mode_check = 0

    try:
        while True:
            now = time.time()

            # Periodically enforce STD mode
            if now - last_mode_check > MODE_CHECK_INTERVAL:
                pam.ensure_std_mode()
                last_mode_check = now

            # Read current function
            func_val = pam.read_function()
            if func_val is None:
                time.sleep(0.1)
                continue
            func = int(func_val)

            # ---------------- FUNCTION 196 ----------------
            if func == 196:
                mode_a = pam.read_ain_mode('A')
                mode_b = pam.read_ain_mode('B')
                wa = pam.read_wa()
                wb = pam.read_wb()
                ia = pam.read_ia()
                ib = pam.read_ib()

                # Update display
                if mode_a:
                    dwin.send_mode(mode_a)

                if wa is not None:
                    scaled = dwin.scale_value(wa, mode_a, 196)
                    dwin.send_value(VPIN_WA, scaled)

                if wb is not None:
                    scaled = dwin.scale_value(wb, mode_b, 196)
                    dwin.send_value(VPIN_WB, scaled)

                if ia is not None:
                    dwin.send_value(VPIN_IA, ia / 10.0)

                if ib is not None:
                    dwin.send_value(VPIN_IB, ib / 10.0)

                dwin.send_value(VPIN_TEMP, 24.0)

                # Save for BLE
                state.update(
                    FUNC=func,
                    WA=dwin.scale_value(wa, mode_a, 196) if wa is not None else None,
                    WB=dwin.scale_value(wb, mode_b, 196) if wb is not None else None,
                    IA=ia,
                    IB=ib,
                    MODE=mode_a
                )

            # ---------------- FUNCTION 195 ----------------
            elif func == 195:
                mode_a = pam.read_ain_mode('A')
                wa = pam.read_w()      # uses 'W' command
                ia = pam.read_ia()
                ib = pam.read_ib()

                if mode_a:
                    dwin.send_mode(mode_a)

                if wa is not None:
                    scaled = dwin.scale_value(wa, mode_a, 195)
                    dwin.send_value(VPIN_WA, scaled)

                dwin.send_value(VPIN_WB, 0.0)

                if ia is not None:
                    dwin.send_value(VPIN_IA, ia / 10.0)

                if ib is not None:
                    dwin.send_value(VPIN_IB, ib / 10.0)

                dwin.send_value(VPIN_TEMP, 24.0)

                state.update(
                    FUNC=func,
                    WA=dwin.scale_value(wa, mode_a, 195) if wa is not None else None,
                    WB=0.0,
                    IA=ia,
                    IB=ib,
                    MODE=mode_a
                )

            else:
                # Unknown function – still update state but nothing to display?
                state.update(FUNC=func)

            time.sleep(MAIN_LOOP_DELAY)

    except KeyboardInterrupt:
        print("\n--- System stopped ---")

if __name__ == "__main__":
    main()