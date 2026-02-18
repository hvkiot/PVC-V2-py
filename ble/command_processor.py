# command_processor.py
import threading
import queue
import time
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Any, Callable
import traceback


class CommandType(Enum):
    """All possible command types"""
    CHANGE_MODE = "change_mode"
    SET_AIN_MODE = "set_ain_mode"
    SET_CURRENT = "set_current"
    GET_STATUS = "get_status"
    SAVE_SETTINGS = "save_settings"


@dataclass
class Command:
    """Immutable command object"""
    type: CommandType
    params: dict[str, Any]
    response_queue: Optional[queue.Queue] = None
    timestamp: float = time.time()


class CommandResult:
    """Command execution result"""

    def __init__(self, success: bool, message: str = "", data: Any = None):
        self.success = success
        self.message = message
        self.data = data


class CommandProcessor:
    """
    Centralized command processor that handles all hardware commands
    in a single thread with a queue-based architecture.
    """

    def __init__(self, pam_controller, state):
        self.pam = pam_controller
        self.state = state
        self.command_queue = queue.Queue(maxsize=50)
        self.running = True

        # Start processing thread
        self.thread = threading.Thread(target=self._process_loop, daemon=True)
        self.thread.start()

        print("✅ Command processor started")

    def submit(self, cmd_type: CommandType, params: dict, wait_for_response: bool = False) -> Optional[CommandResult]:
        """
        Submit a command to be processed

        Args:
            cmd_type: Type of command
            params: Command parameters
            wait_for_response: If True, wait for result (blocking)

        Returns:
            CommandResult if wait_for_response=True, else None
        """
        response_queue = queue.Queue() if wait_for_response else None
        cmd = Command(cmd_type, params, response_queue)

        try:
            self.command_queue.put(cmd, block=False)

            if wait_for_response:
                try:
                    result = response_queue.get(timeout=5.0)
                    return result
                except queue.Empty:
                    return CommandResult(False, "Command timeout")

            return CommandResult(True, "Command queued")

        except queue.Full:
            return CommandResult(False, "Command queue full")

    def stop(self):
        """Stop the processor"""
        self.running = False

    def _process_loop(self):
        """Main processing loop - runs in dedicated thread"""
        while self.running:
            try:
                # Get next command (with timeout to allow checking running flag)
                cmd = self.command_queue.get(timeout=0.5)

                # Process the command
                result = self._execute_command(cmd)

                # Send response if requested
                if cmd.response_queue:
                    cmd.response_queue.put(result)

            except queue.Empty:
                # No commands, continue
                continue
            except Exception as e:
                print(f"❌ Command processor error: {e}")
                traceback.print_exc()

    def _execute_command(self, cmd: Command) -> CommandResult:
        """Execute a single command"""
        try:
            handlers = {
                CommandType.CHANGE_MODE: self._handle_change_mode,
                CommandType.SET_AIN_MODE: self._handle_set_ain_mode,
                CommandType.SET_CURRENT: self._handle_set_current,
                CommandType.SAVE_SETTINGS: self._handle_save_settings,
                CommandType.GET_STATUS: self._handle_get_status,
            }

            handler = handlers.get(cmd.type)
            if handler:
                return handler(cmd)
            else:
                return CommandResult(False, f"Unknown command type: {cmd.type}")

        except Exception as e:
            return CommandResult(False, f"Command execution error: {e}")

    def _handle_change_mode(self, cmd: Command) -> CommandResult:
        try:
            new_mode = cmd.params.get('mode')
            if new_mode not in [195, 196]:
                return CommandResult(False, f"Invalid mode: {new_mode}")

            old_mode = self.state.get('FUNC')
            print(f"📌 Mode change: {old_mode} → {new_mode}")

            # Set transition flag
            self.state.set_transition(True)

            # Execute mode change
            success = self.pam.change_pam_function(new_mode)

            if success:
                if new_mode == 196 and old_mode == 195:
                    # Special handling: 195 → 196 transition
                    print("📌 195→196: Setting up both channels atomically")

                    # Read current AINA mode (from 195 mode)
                    current_mode = self.pam.read_ain_mode('A')
                    if not current_mode:
                        current_mode = 'V'  # Default

                    # Set both channels to the same mode
                    print(f"📌 Setting AINA to {current_mode}")
                    self.pam.write_ain_mode(current_mode, 'A')
                    time.sleep(0.1)

                    print(f"📌 Setting AINB to {current_mode}")
                    self.pam.write_ain_mode(current_mode, 'B')
                    time.sleep(0.1)

                    # Save settings
                    self.pam.save_pam_settings()
                    time.sleep(0.5)  # Wait for EEPROM

                    # Verify both are set
                    new_mode_a = self.pam.read_ain_mode('A')
                    new_mode_b = self.pam.read_ain_mode('B')
                    print(f"✅ Verified - A: {new_mode_a}, B: {new_mode_b}")

                    # Update state
                    self.state.update(
                        FUNC=new_mode,
                        MODE_A=new_mode_a,
                        MODE_B=new_mode_b
                    )
                else:
                    # Normal mode change
                    self.state.update(FUNC=new_mode)

                # Clear transition flag
                self.state.set_transition(False)
                return CommandResult(True, f"Mode changed to {new_mode}")
            else:
                self.state.set_transition(False)
                return CommandResult(False, "Mode change failed")

        except Exception as e:
            self.state.set_transition(False)
            return CommandResult(False, f"Mode change error: {e}")

    def _handle_set_ain_mode(self, cmd: Command) -> CommandResult:
        try:
            unit = cmd.params.get('unit')
            channel = cmd.params.get('channel', 'A')

            if unit not in ['V', 'C']:
                return CommandResult(False, f"Invalid unit: {unit}")

            current_mode = self.state.get('FUNC')

            # If in mode 196 and setting one channel, we should set both
            if current_mode == 196:
                print(
                    f"📌 Mode 196: Setting both channels to {unit} atomically")

                self.state.set_transition(True)

                # Set both channels
                success_a = self.pam.change_pam_ain_mode(unit, 'A')
                time.sleep(0.1)
                success_b = self.pam.change_pam_ain_mode(unit, 'B')

                if success_a and success_b:
                    self.state.update(
                        MODE_A=unit,
                        MODE_B=unit
                    )
                    self.state.set_transition(False)
                    return CommandResult(True, f"Both channels set to {unit}")
                else:
                    self.state.set_transition(False)
                    return CommandResult(False, "Failed to set both channels")
            else:
                # Mode 195 - just set AINA
                print(f"📌 Setting AINA to {unit}")

                self.state.set_transition(True)
                success = self.pam.change_pam_ain_mode(unit, channel)

                if success:
                    self.state.update(MODE=unit)
                    self.state.set_transition(False)
                    return CommandResult(True, f"AIN{channel} set to {unit}")
                else:
                    self.state.set_transition(False)
                    return CommandResult(False, "AIN mode change failed")

        except Exception as e:
            self.state.set_transition(False)
            return CommandResult(False, f"AIN mode error: {e}")

    def _handle_set_current(self, cmd: Command) -> CommandResult:
        """Handle current setting"""
        try:
            value = cmd.params.get('value')
            channel = cmd.params.get('channel', 'A')
            mode = cmd.params.get('mode')

            # Validate
            if not isinstance(value, int):
                return CommandResult(False, f"Invalid value type: {type(value)}")

            if not (500 <= value <= 2600):
                return CommandResult(False, f"Value {value} out of range (500-2600)")

            print(
                f"📌 Setting current - Mode:{mode}, Channel:{channel}, Value:{value}mA")

            # Set transition flag
            self.state.set_transition(True)

            # Use appropriate method based on mode
            if mode == 195:
                # Mode 195: Use CURRENT command (affects channel A)
                success = self.pam.set_current_value(value, 'A', str(mode))
                if success:
                    self.state.update(CURRENT_STATUS=value)
            else:  # 196
                # Mode 196: Use channel-specific command
                success = self.pam.set_current_value(value, channel, str(mode))
                if success:
                    if channel == 'A':
                        self.state.update(CURRENT_A_STATUS=value)
                    else:
                        self.state.update(CURRENT_B_STATUS=value)

            # Clear transition flag
            self.state.set_transition(False)

            if success:
                return CommandResult(True, f"Current {channel}={value}mA")
            else:
                return CommandResult(False, "Failed to set current")

        except Exception as e:
            self.state.set_transition(False)
            return CommandResult(False, f"Current setting error: {e}")

    def _handle_save_settings(self, cmd: Command) -> CommandResult:
        """Save settings to EEPROM"""
        try:
            self.pam.save_pam_settings()
            return CommandResult(True, "Settings saved")
        except Exception as e:
            return CommandResult(False, f"Save failed: {e}")

    def _handle_get_status(self, cmd: Command) -> CommandResult:
        """Get current status"""
        try:
            data = self.state.get_all()
            return CommandResult(True, "Status retrieved", data)
        except Exception as e:
            return CommandResult(False, f"Status error: {e}")
