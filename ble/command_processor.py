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
        """Handle mode change (195/196)"""
        try:
            new_mode = cmd.params.get('mode')
            if new_mode not in [195, 196]:
                return CommandResult(False, f"Invalid mode: {new_mode}")

            print(f"📌 Executing mode change to {new_mode}")

            # Execute mode change
            success = self.pam.change_pam_function(new_mode)

            if success:
                # IMPORTANT: When switching to 195, ensure both channels have same mode
                if new_mode == 195:
                    print("📌 Mode 195: Syncing AIN modes...")
                    # Read current AINA mode
                    current_mode = self.pam.read_ain_mode('A')
                    if current_mode:
                        # Set AINB to match AINA (even though it's not used in 195)
                        self.pam.write_ain_mode(current_mode, 'B')
                        self.pam.save_pam_settings()
                        print(f"✅ Synced AINB to {current_mode}")

                # Update state
                self.state.update(FUNC=new_mode)
                return CommandResult(True, f"Mode changed to {new_mode}")
            else:
                return CommandResult(False, "Mode change failed")

        except Exception as e:
            return CommandResult(False, f"Mode change error: {e}")

    def _handle_set_ain_mode(self, cmd: Command) -> CommandResult:
        """Handle AIN mode setting (Voltage/Current)"""
        try:
            unit = cmd.params.get('unit')  # 'V' or 'C'
            channel = cmd.params.get('channel', 'A')

            if unit not in ['V', 'C']:
                return CommandResult(False, f"Invalid unit: {unit}")

            print(f"📌 Setting AIN{channel} to {unit}")

            success = self.pam.change_pam_ain_mode(unit, channel)

            if success:
                # Update state based on mode
                current_mode = self.state.get('FUNC')
                if current_mode == 196:
                    # Update the specific channel's mode
                    if channel == 'A':
                        self.state.update(MODE_A=unit)
                    else:
                        self.state.update(MODE_B=unit)
                else:
                    self.state.update(MODE=unit)

                return CommandResult(True, f"AIN{channel} set to {unit}")
            else:
                return CommandResult(False, "AIN mode change failed")

        except Exception as e:
            return CommandResult(False, f"AIN mode error: {e}")

    def _handle_set_current(self, cmd: Command) -> CommandResult:
        """Handle current setting"""
        try:
            value = cmd.params.get('value')
            channel = cmd.params.get('channel', 'A')
            mode = cmd.params.get('mode')  # 195 or 196

            # Validate
            if not isinstance(value, (int, float)):
                return CommandResult(False, f"Invalid value type: {type(value)}")

            value = int(value)
            if not (500 <= value <= 2600):
                return CommandResult(False, f"Value {value} out of range (500-2600)")

            print(f"📌 Setting current {channel}={value}mA in mode {mode}")

            # Use appropriate method based on mode
            if mode == 195:
                success = self.pam.set_current_value(value, 'A', str(mode))
            else:  # 196
                success = self.pam.set_current_value(value, channel, str(mode))

            if success:
                # Update state
                if mode == 195:
                    self.state.update(CURRENT_STATUS=value)
                else:
                    if channel == 'A':
                        self.state.update(CURRENT_A_STATUS=value)
                    else:
                        self.state.update(CURRENT_B_STATUS=value)

                return CommandResult(True, f"Current {channel}={value}mA")
            else:
                return CommandResult(False, "Failed to set current")

        except Exception as e:
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
