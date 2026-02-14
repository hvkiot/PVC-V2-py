#!/usr/bin/env python3
# main.py
import time
import threading
import traceback
import sys

from config import (
    MAIN_LOOP_DELAY, MODE_CHECK_INTERVAL,
    VPIN_WA, VPIN_WB, VPIN_IA, VPIN_IB, VPIN_TEMP
)
from state import MachineState
from hardware.pam import PAMController
from hardware.dwin import DWINDisplay
from ble.gatt_server import run_ble_server


def safe_execution(func, default=None, error_msg=None):
    """Execute a function safely, return default on error."""
    try:
        return func()
    except Exception as e:
        if error_msg:
            print(f"⚠️ {error_msg}: {e}")
        return default


def main_loop(state, pam, dwin):
    """Main processing loop - isolated so it can be restarted."""
    last_mode_check = 0
    loop_count = 0

    while True:
        try:
            now = time.time()
            loop_count += 1

            # Periodically enforce STD mode
            if now - last_mode_check > MODE_CHECK_INTERVAL:
                safe_execution(
                    pam.ensure_std_mode,
                    error_msg="PAM mode check failed"
                )
                last_mode_check = now

            # Read current function - this is critical, skip if fails
            func_val = safe_execution(pam.read_function)
            if func_val is None:
                time.sleep(0.2)  # Longer sleep if no function
                continue

            func = int(func_val)

            # Initialize variables
            mode_a = mode_b = None
            wa = wb = ia = ib = None
            scaled_wa = scaled_wb = None

            # ---------------- FUNCTION 196 ----------------
            if func == 196:
                # Read all values safely
                mode_a = safe_execution(lambda: pam.read_ain_mode('A'))
                mode_b = safe_execution(lambda: pam.read_ain_mode('B'))
                wa = safe_execution(pam.read_wa)
                wb = safe_execution(pam.read_wb)
                ia = safe_execution(pam.read_ia)
                ib = safe_execution(pam.read_ib)
                ready = safe_execution(pam.get_ready_status)

                # Update display - with safe execution for each operation
                if mode_a:
                    safe_execution(
                        lambda: dwin.send_mode(mode_a),
                        error_msg="DWIN send_mode failed"
                    )

                # WA - only if both wa and mode_a are valid
                if wa is not None and mode_a is not None:
                    scaled_wa = safe_execution(
                        lambda: dwin.scale_value(wa, mode_a, 196)
                    )
                    if scaled_wa is not None:
                        safe_execution(
                            lambda: dwin.send_value(VPIN_WA, scaled_wa),
                            error_msg=f"DWIN send_value WA failed: {scaled_wa}"
                        )

                # WB - only if both wb and mode_b are valid
                if wb is not None and mode_b is not None:
                    scaled_wb = safe_execution(
                        lambda: dwin.scale_value(wb, mode_b, 196)
                    )
                    if scaled_wb is not None:
                        safe_execution(
                            lambda: dwin.send_value(VPIN_WB, scaled_wb),
                            error_msg=f"DWIN send_value WB failed: {scaled_wb}"
                        )

                # IA/IB - always send if not None, else send 0
                ia_val = ia / 10.0 if ia is not None else 0.0
                safe_execution(
                    lambda: dwin.send_value(VPIN_IA, ia_val),
                    error_msg=f"DWIN send_value IA failed: {ia_val}"
                )

                ib_val = ib / 10.0 if ib is not None else 0.0
                safe_execution(
                    lambda: dwin.send_value(VPIN_IB, ib_val),
                    error_msg=f"DWIN send_value IB failed: {ib_val}"
                )

                # Temperature - always send 24.0
                safe_execution(
                    lambda: dwin.send_value(VPIN_TEMP, 24.0),
                    error_msg="DWIN send_value TEMP failed"
                )

                # Save for BLE - with None handling
                state.update(
                    FUNC=func,
                    WA=scaled_wa,
                    WB=scaled_wb,
                    IA=ia_val,
                    IB=ib_val,
                    MODE=mode_a if mode_a is not None else "UNKNOWN",
                    READY=ready
                )

            # ---------------- FUNCTION 195 ----------------
            elif func == 195:
                mode_a = safe_execution(lambda: pam.read_ain_mode('A'))
                wa = safe_execution(pam.read_w)  # uses 'W' command
                ia = safe_execution(pam.read_ia)
                ib = safe_execution(pam.read_ib)
                ready = safe_execution(pam.get_ready_status)

                if mode_a:
                    safe_execution(
                        lambda: dwin.send_mode(mode_a),
                        error_msg="DWIN send_mode failed"
                    )

                # WA - only if both wa and mode_a are valid
                if wa is not None and mode_a is not None:
                    scaled_wa = safe_execution(
                        lambda: dwin.scale_value(wa, mode_a, 195)
                    )
                    if scaled_wa is not None:
                        safe_execution(
                            lambda: dwin.send_value(VPIN_WA, scaled_wa),
                            error_msg=f"DWIN send_value WA failed: {scaled_wa}"
                        )

                # WB is always 0 for function 195
                safe_execution(
                    lambda: dwin.send_value(VPIN_WB, 0.0),
                    error_msg="DWIN send_value WB failed"
                )

                # IA/IB
                ia_val = ia / 10.0 if ia is not None else 0.0
                safe_execution(
                    lambda: dwin.send_value(VPIN_IA, ia_val),
                    error_msg=f"DWIN send_value IA failed: {ia_val}"
                )

                ib_val = ib / 10.0 if ib is not None else 0.0
                safe_execution(
                    lambda: dwin.send_value(VPIN_IB, ib_val),
                    error_msg=f"DWIN send_value IB failed: {ib_val}"
                )

                safe_execution(
                    lambda: dwin.send_value(VPIN_TEMP, 24.0),
                    error_msg="DWIN send_value TEMP failed"
                )

                state.update(
                    FUNC=func,
                    WA=scaled_wa,
                    WB=0.0,
                    IA=ia_val,
                    IB=ib_val,
                    MODE=mode_a if mode_a is not None else "UNKNOWN",
                    READY=ready
                )

            else:
                # Unknown function – still update state
                state.update(FUNC=func, MODE="UNKNOWN")

            time.sleep(MAIN_LOOP_DELAY)

        except KeyboardInterrupt:
            print("\n🛑 Keyboard interrupt received, shutting down...")
            raise  # Re-raise to be caught by outer handler

        except Exception as e:
            # This catches ANY unexpected error in the main loop
            print(f"\n💥 CRITICAL ERROR in main loop: {e}")
            traceback.print_exc()
            print("🔄 Restarting main loop in 2 seconds...\n")
            time.sleep(2)
            # Continue the while loop - it will restart from the top
            continue


def main():
    """Main entry point with full system recovery."""
    print("=" * 50)
    print("🚀 PVC-V2 System Starting")
    print("=" * 50)

    # Global error handler for uncaught exceptions
    def global_exception_handler(exc_type, exc_value, exc_traceback):
        print(
            f"\n💥 UNCAUGHT GLOBAL EXCEPTION: {exc_type.__name__}: {exc_value}")
        traceback.print_tb(exc_traceback)
        print("\n🔄 System will restart in 5 seconds...\n")
        time.sleep(5)
        # Restart the entire system
        main()  # Recursive restart - will create new instance

    sys.excepthook = global_exception_handler

    # Persistent state across restarts
    state = MachineState()

    # BLE server runs in background and will auto-reconnect
    ble_thread_running = False

    # Main system restart loop
    while True:
        try:
            print("\n--- Initializing hardware ---")

            # Initialize hardware with retries built into the classes
            pam = PAMController()
            dwin = DWINDisplay()

            # Start BLE server only once
            if not ble_thread_running:
                print("--- Starting BLE server ---")
                ble_thread = threading.Thread(
                    target=run_ble_server,
                    args=(state,),
                    daemon=True,
                    name="BLE-Thread"
                )
                ble_thread.start()
                ble_thread_running = True
                time.sleep(1)  # Give BLE time to initialize

            print("--- System running (press Ctrl+C to stop) ---\n")

            # Run the main processing loop
            main_loop(state, pam, dwin)

        except KeyboardInterrupt:
            print("\n\n🛑 System shutdown complete")
            break

        except Exception as e:
            print(f"\n💥 SYSTEM ERROR: {e}")
            traceback.print_exc()
            print("\n🔄 Reinitializing entire system in 3 seconds...\n")
            time.sleep(3)

            # Clean up old connections
            try:
                if 'pam' in locals():
                    pam.ser.ser.close()
            except:
                pass
            try:
                if 'dwin' in locals():
                    dwin.ser.ser.close()
            except:
                pass

            # Restart the while loop - reinitialize everything
            continue


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
    except Exception as e:
        print(f"\n💥 FATAL: {e}")
        traceback.print_exc()
        print("\n⚠️  System crashed. Restart manually.")
