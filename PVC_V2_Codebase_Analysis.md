# PVC-V2-py Codebase Analysis — Full Project Context for ESP32 Arduino Migration

## 1. Project Overview

**PVC-V2-py** is a Python control system for a **PAM (Programmable Analog Module)** — a programmable power supply / current source device. It runs on a Linux single-board computer (likely Raspberry Pi) and provides three interfaces:

1. **Serial command interface** to the PAM power hardware (RS-232 / USB-serial)
2. **Serial display interface** to a DWIN HMI touch display
3. **Bluetooth Low Energy (BLE) GATT server** for remote control via a mobile app (Android/iOS)

The system reads measurement values (current, voltage/power), modes, and status from the PAM, displays them on the DWIN screen, and simultaneously broadcasts them over BLE as notifications. It also accepts commands from BLE (mode switching, current setting, AIN mode changes) and from the DWIN touch display (AIN mode selection via page 28).

---

## 2. Hardware Architecture

```
┌─────────────────┐     Serial (57600 baud)     ┌──────────────────────┐
│   DWIN Display  │◄──────────────────────────►│  Linux Host (RPi)    │
│  (HMI Touch)    │     DWIN Protocol           │  (Python Software)   │
│  /dev/serial0   │     (115200 baud)           │                      │
└─────────────────┘                              │  ┌────────────────┐  │
                                                 │  │  PAMController │  │
┌─────────────────┐     Serial (57600 baud)     │  │  (Serial I/F)  │  │
│   PAM Module    │◄──────────────────────────►│  └────────────────┘  │
│  (Power Supply) │     Text Protocol           │                      │
│  /dev/ttyUSB0   │     (57600 baud)            │  ┌────────────────┐  │
└─────────────────┘                              │  │  BLE GATT      │  │
                                                 │  │  Server        │  │
┌─────────────────┐     BLE (Wireless)          │  └────────────────┘  │
│  Mobile App     │◄──────────────────────────►│                      │
│  (Android/iOS)  │     GATT Notifications      └──────────────────────┘
└─────────────────┘
```

---

## 3. Operating Modes (The PAM "FUNCTION" concept)

The PAM controller operates in two distinct modes, which is the central design concept:

### Mode 195 — "Directional" (Single Channel)
- Only **channel A** is active for current control
- Uses `W` command (single winding) — reads WA value only
- WB is forced to 0.0 on display
- Current set via `CURRENT <value>` (not per-channel)
- AIN mode only configurable for channel A
- State reports: `CURRENT_STATUS` (single value)

### Mode 196 — "Dual Throttle" (Two Channels)
- Both **channel A and channel B** are active
- Uses `WA` and `WB` commands (separate windings)
- Current set per-channel: `CURRENT:A <value>` / `CURRENT:B <value>`
- AIN mode must be set identically on both channels (when changing channel A mode from BLE, channel B is also set)
- If AIN modes of A and B differ, DWIN switches to **page 28** to prompt user to synchronize them
- State reports: `CURRENT_A_STATUS`, `CURRENT_B_STATUS` (separate values)

### Switching Between Modes
When switching 195 → 196:
1. Set new function mode
2. Zero out IA and IB (currents)
3. Save to EEPROM
4. Read current AINA mode, apply same mode to both AINB
5. Save again
6. Verify both channels

---

## 4. Data Model & Scaling

### Measurement Values

| Parameter | Source Command | Raw Range | Display Scaling | Units |
|-----------|---------------|-----------|-----------------|-------|
| WA (Winding A) | `WA` or `W` | Float | /1000 (V mode), or *0.0016+4 (C mode 196), or *0.0008+12 (C mode 195) | V or mA |
| WB (Winding B) | `WB` | Float | Same as WA | V or mA |
| IA (Current A) | `IA` | Integer | /10 | A (Amps) |
| IB (Current B) | `IB` | Integer | /10 | A (Amps) |
| Temp | N/A (fixed) | — | Always 24.0 | °C |

### AIN Mode (Analog Input Mode)
- **V** (Voltage) mode: raw/1000 → volts
- **C** (Current) mode: Current-loop scaling, different formula per function mode

### DWIN VPIN Address Map

| VPIN | Address | Content |
|------|---------|---------|
| VPIN_WA | `0x5500` | Scaled WA value |
| VPIN_WB | `0x5600` | Scaled WB value |
| VPIN_IA | `0x5700` | IA / 10 |
| VPIN_IB | `0x5800` | IB / 10 |
| VPIN_TEMP | `0x5900` | Temperature (24.0 fixed) |
| VPIN_MODE_ADDR | `0x5000` | Mode byte (V=0, C=1) |
| VP_5100 | `0x5100` | Touch selection (0=V, 1=C) on page 28 |

### BLE Notification Packet Format
```
FUNC:{func},WA:{wa},WB:{wb},IA:{ia},IB:{ib},MODE:{mode},READY:{ready},PIN15:{pin15},PIN6:{pin6},ENABLED_B:{enabled_b},CURRENT_A_STATUS:{ca},CURRENT_B_STATUS:{cb},CURRENT_STATUS:{cs}\n
```
Sent every 200ms as GATT notification.

---

## 5. File-by-File Breakdown

### Root Files

#### `config.py` — All Configuration Constants
```python
# Hardware ports
PAM_PORT = "/dev/ttyUSB0"    # PAM on USB-serial
DWIN_PORT = "/dev/serial0"   # DWIN on UART

# Baud rates
PAM_BAUD = 57600
DWIN_BAUD = 115200

# Timing
PAM_CMD_DELAY = 0.0
MAIN_LOOP_DELAY = 0.005      # 5ms main loop cycle
MODE_CHECK_INTERVAL = 3.0    # Verify STD mode every 3s

# BLE
SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0"
CHAR_UUID   = "12345678-1234-5678-1234-56789abcdef1"
BLE_DEVICE_NAME = os.getenv("DEVICE_SUFFIX")  # from .env: "PVC-26020001"

# DWIN VPIN addresses (0x5500-0x5900, 0x5000, 0x5100)
```
**For ESP32**: All these become `#define` constants. Ports become `UART_NUM_1`, `UART_NUM_2`. BLE UUIDs stay as strings.

#### `state.py` — MachineState Class
Thread-safe container with a `_data` dictionary and a `threading.Lock()`.

**Keys stored:**
```
IN_TRANSITION, FUNC, WA, WB, IA, IB, MODE, READY,
PIN15, PIN6, ENABLED_B, CURRENT_A_STATUS, CURRENT_B_STATUS, CURRENT_STATUS
```

Methods: `update()`, `get_all()`, `get()`, `set_transition()`, `is_in_transition()`, `wait_for_transition()`

**For ESP32**: Replace lock with `portMUX_TYPE` / `mutex`. Use a simple struct or global variables with mutex protection.

#### `main.py` — Main Entry & Orchestration Loop

**Architecture:**
```
main()
├── global_exception_handler()  — recursive restart
├── MachineState() instance
├── PAMController() instance
├── DWINDisplay() instance
├── CommandProcessor(pam, state) — background thread
├── BLE thread (run_ble_server) — background thread (daemon)
└── main_loop() — foreground infinite loop
```

**main_loop() flow:**
```
while True:
    1. Check write_lock (skip if PAM busy)
    2. Check IN_TRANSITION flag (skip during mode changes)
    3. Every 3s: ensure PAM is in STD mode
    4. Read PAM FUNCTION (mode 195 or 196)
    5. if func == 196:
        - Read A/B modes, WA, WB, IA, IB, ready, pin15, pin6, enabled_b, currents
        - If mode_a != mode_b: switch DWIN to page 28, poll VP5100 for user selection
        - Send mode, scaled values to DWIN
        - Update state
    6. elif func == 195:
        - Read A mode, W, IA, IB, ready, pins, current
        - Send mode, scaled values (WB=0) to DWIN
        - Update state
    7. else: update state with UNKNOWN mode
    8. sleep(MAIN_LOOP_DELAY = 5ms)
```

**For ESP32**: This becomes `loop()` in Arduino. No threading needed — BLE, serial, and display all in `loop()` using non-blocking patterns.

#### `test.py` — Quick Test
Sends `RC:910` command to PAM every second. Used for debugging.

---

### hardware/ Package

#### `pam.py` — PAMController Class (463 lines)

**Serial Protocol to PAM:**
- Text-based, command-response with `>` prompt delimiter
- Commands sent as `COMMAND\r\n`
- Response read until `>` appears (200ms timeout)
- Baud: 57600

**ALL PAM Commands:**

| Command | Returns | Description |
|---------|---------|-------------|
| `FUNCTION` | number (195/196) | Read current operating mode |
| `FUNCTION <n>` | — | Set operating mode |
| `AINA` | "V" or "C" | Read AIN mode of channel A |
| `AINB` | "V" or "C" | Read AIN mode of channel B |
| `AINA V` / `AINA C` | — | Set AIN mode for channel A |
| `AINB V` / `AINB C` | — | Set AIN mode for channel B |
| `WA` | float | Read Winding A value |
| `WB` | float | Read Winding B value |
| `W` | float | Read single winding (mode 195) |
| `IA` | number | Read Current A |
| `IB` | number | Read Current B |
| `RX1:READYA` | number | Read status word (bits decoded in get_ready_status) |
| `RC:S` | number | Remote control status (bits for PIN15=bit6, PIN6=bit3) |
| `CURRENT <v>` | — | Set current in mode 195 |
| `CURRENT:A <v>` | — | Set current channel A in mode 196 |
| `CURRENT:B <v>` | — | Set current channel B in mode 196 |
| `CURRENT` | number | Read current in mode 195 |
| `CURRENT:A` | number | Read current A in mode 196 |
| `CURRENT:B` | number | Read current B in mode 196 |
| `ENABLE_B` | "ON"/"OFF" | Read enable B status |
| `IA 0` / `IB 0` | — | Zero out currents during mode switch |
| `MODE` | "STD"/"EXP" | Read PAM operating mode |
| `MODE STD` | — | Force STD mode |
| `SAVE` | — | Save settings to EEPROM |

**Status Word Decoding (RX1:READYA):**
- **Mode 196**: Bit14=chA active, Bit15=chB active
- **Mode 195**: Bit7=chA_ok, Bit8=chB_ok, 65532=ALL OFF

**RC:S Bit Decoding:**
- Bit 3 (value 8): PIN 6 status
- Bit 6 (value 64): PIN 15 status

**Current Value Range:** 500–2600 mA

#### `dwin.py` — DWINDisplay Class (90 lines)

**DWIN Protocol:**
- Proprietary packet format: `5A A5 <length> <command> <address> <data>`
- Command `0x82` = write variable, `0x83` = read variable
- Baud: 115200

**Key Methods:**
- `send_value(vpin, float_value)` — Scale float to int16 (×10), clamp to [-32768, 32767], write with caching (skip if unchanged)
- `send_mode(mode)` — V→0, C→1, write to VPIN 0x5000
- `switch_page(page_id)` — Send page switch command to `0x0084`
- `read_vp_5100(timeout=2s)` — Poll register 0x5100, return 8-byte response last 2 bytes as uint16
- `scale_value(raw, mode, function)` — Static method applying voltage/current scaling formulas

**Scaling Formulas:**
| Mode | Function 196 | Function 195 |
|------|-------------|-------------|
| V | raw / 1000.0 | raw / 1000.0 |
| C | (raw * 0.0016) + 4.0 | min(20, max(4, (raw * 0.0008) + 12.0)) |

---

### ble/ Package

#### `gatt_server.py` — BLE GATT Server (Linux BlueZ / D-Bus)

**Architecture:** Uses `dbus-python` + `PyGObject` (GLib main loop) to register a BlueZ GATT server.

**GATT Structure:**
- **Service UUID**: `12345678-1234-5678-1234-56789abcdef0`
- **Characteristic UUID**: `12345678-1234-5678-1234-56789abcdef1`
- **Characteristic Properties**: read, notify, write
- **Device Name**: From `.env` file (`PVC-26020001`)
- **Advertisement**: "peripheral" type, no bonding (Flags=0x04)

**BLE Incoming Commands (WriteValue parsing):**

| Received String | Command Type | Params |
|----------------|-------------|--------|
| `"195"` | CHANGE_MODE | `{"mode": 195}` |
| `"196"` | CHANGE_MODE | `{"mode": 196}` |
| `"VOLTAGE"` | SET_AIN_MODE | `{"unit": "V"}` |
| `"CURRENT"` | SET_AIN_MODE | `{"unit": "C"}` |
| `"CUR:1500:195"` | SET_CURRENT | `{"channel":"A", "mode":195, "value":1500}` |
| `"CURA:1600:196"` | SET_CURRENT | `{"channel":"A", "mode":196, "value":1600}` |
| `"CURB:1200:196"` | SET_CURRENT | `{"channel":"B", "mode":196, "value":1200}` |

**BLE Outgoing (Notifications):** Sends state packet every 200ms (see section 4).

#### `command_processor.py` — Queue-Based Command Processor

A dedicated thread processes commands from a `queue.Queue(maxsize=50)`. Handles:

| CommandType | Handler | Behavior |
|------------|---------|----------|
| CHANGE_MODE | `_handle_change_mode` | Set transition flag → change function → if 195→196, sync AIN modes → save → verify → clear flag |
| SET_AIN_MODE | `_handle_set_ain_mode` | In mode 196: set both A+B to same unit atomically. In 195: set AINA only. |
| SET_CURRENT | `_handle_set_current` | Set transition flag → call pam.set_current_value → update state → clear flag |
| SAVE_SETTINGS | `_handle_save_settings` | Call pam.save_pam_settings() |
| GET_STATUS | `_handle_get_status` | Return snapshot of state.get_all() |

#### `bluez_helpers.py` — BlueZ D-Bus Helpers
Small helper to find the first BLE adapter supporting both GATT and LE Advertising managers.

---

### utils/ Package

#### `serial_reconnect.py` — Robust Serial Wrapper (122 lines)

Wraps `pySerial` with:
- **Auto-reconnect**: Retries `_open()` forever on failure
- **Thread-safe**: All operations protected by `threading.Lock()`
- **Error recovery**: Every read/write/reset/flush catches exceptions, calls `_reopen()`, and retries once
- Methods: `write()`, `read()`, `read_all()`, `reset_input_buffer()`, `flush()`, `in_waiting` property

---

## 6. State Machine / Transitions

### Transition Flag System
The `IN_TRANSITION` flag is critical:
- **Set** at the start of any mode/current/AIN change operation
- **Cleared** after completion (success or failure)
- **Checked** in `main_loop()` — skips PAM reads during transition
- **Purpose**: Prevents the main loop from reading inconsistent intermediate states

### AIN Mode Mismatch Handling (Mode 196 only)
1. If `mode_a != mode_b`, DWIN switches to **page 28**
2. Page 28 presents user with V/C selection
3. `read_vp_5100()` polls for selection (0=V, 1=C)
4. Corresponding `SET_AIN_MODE` command submitted to CommandProcessor
5. After transition completes, main loop returns to normal

---

## 7. Error Handling & Resilience Strategy

| Layer | Mechanism |
|-------|-----------|
| **Serial** | Auto-reconnect on any read/write failure, retry once |
| **PAM Commands** | Try/except with empty string return on failure |
| **DWIN Commands** | Try/except with `safe_execution()` decorator |
| **Main Loop** | `safe_execution()` wrapper returns `None` on failure; loop continues |
| **BLE Thread** | Daemon thread; restarts automatically if crashed |
| **Command Processor** | Separate thread with queue; errors logged, processing continues |
| **Global** | `sys.excepthook` catches uncaught exceptions, recursively calls `main()` after 5s |
| **System Loop** | Outer `while True` in `main()` re-initializes everything on error |

---

## 8. Timing & Loop Rates

| Process | Rate / Interval | Notes |
|---------|----------------|-------|
| Main loop cycle | 5ms (MAIN_LOOP_DELAY) | 200 Hz nominal, but PAM serial commands take ~200ms each |
| BLE notifications | 200ms | 5 Hz state broadcast |
| STD mode check | 3s | Verifies PAM not in EXP mode |
| PAM serial timeout | 200ms per command | Per cmd() call |
| EEPROM save delay | 3s | After SAVE command |
| Page switch cooldown | 2s | Prevents rapid page 28 toggling |
| Mode/TX transition wait | 0.5s queue timeout, 2s max wait | Command processor |

---

## 9. Key Design Patterns to Preserve in Arduino

1. **Mode-based branching** (195 vs 196) — central to PAM logic
2. **Scaling formulas** per mode × channel — must match exactly
3. **Transition flag** — prevents race conditions during PAM writes
4. **AIN mode synchronization** — mode 196 requires both channels same
5. **DWIN protocol** — 5A A5 packets, VPIN addresses, page switching
6. **BLE GATT** — same UUIDs, same notification format, same command parser
7. **Current range validation** — 500–2600 mA
8. **Robust serial** — auto-retry on error is essential for production

---

## 10. ESP32 Arduino Migration Notes

### Hardware Mapping

| Python Component | ESP32 Equivalent |
|-----------------|-----------------|
| `PAM_PORT = "/dev/ttyUSB0"` | `UART_NUM_1` (GPIO 16/17 or custom) |
| `DWIN_PORT = "/dev/serial0"` | `UART_NUM_2` (GPIO 18/19 or custom) |
| BLE BlueZ/D-Bus | `BLEDevice` / `BLEServer` (ArduinoBLE or ESP32 BLE Arduino library) |
| Threading | No threads needed; use `loop()` with `millis()` timers |
| `threading.Lock()` | `portMUX_TYPE` or `SemaphoreHandle_t` |
| GLib main loop | Not needed; BLE handled by ESP32 BLE stack natively |
| `pyserial` | `HardwareSerial` |

### File Structure for Arduino

```
PVC-V2-ESP32/
├── PVC-V2-ESP32.ino           # setup() + loop() — equivalent of main.py
├── config.h                    # All #defines (ports, bauds, VPINs, UUIDs, timing)
├── MachineState.h/.cpp         # Thread-safe state struct (mutex-protected)
├── PAMController.h/.cpp        # Serial commands to PAM
├── DWINDisplay.h/.cpp          # 5A A5 protocol to DWIN
├── BLEGATTServer.h/.cpp        # BLE service, characteristic, commands
├── CommandProcessor.h/.cpp     # Command queue (ring buffer or FreeRTOS queue)
├── SerialReconnect.h/.cpp      # Robust serial with error recovery
└── scaling.h                   # Static scaling formulas
```

### Critical Timing Considerations
- **PAM serial timeout**: 200ms per command — must use non-blocking reads with timeout
- **EEPROM save delay**: 3 seconds — must not block main loop
- **Main loop**: Should NOT run at 5ms (too fast for PAM serial). Use state-machine approach with `millis()`.
- **BLE notifications**: Every 200ms — use `millis()` timer
- **Mode check**: Every 3 seconds

### BLE Implementation Notes
- ESP32 BLE Arduino library provides `BLEServer`, `BLEService`, `BLECharacteristic` natively
- Same UUIDs can be used directly
- Notification sending: `pCharacteristic->notify()`
- Command receiving: `BLECharacteristicCallbacks::onWrite()`
- No D-Bus, no GLib — much simpler

### Serial Protocol Implementation
- PAM uses text-based commands with `\r\n` termination and `>` prompt
- DWIN uses binary `5A A5` packets
- Both can be handled with `HardwareSerial` + `readBytesUntil()` or custom ring buffers
- Non-blocking implementation critical for ESP32 stability
