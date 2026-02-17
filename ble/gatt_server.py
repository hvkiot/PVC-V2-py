# ble/gatt_server.py
import dbus
import dbus.service
import threading
import time
from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop

from config import SERVICE_UUID, CHAR_UUID, BLE_DEVICE_NAME
from ble.bluez_helpers import find_adapter, GATT_MANAGER_IFACE, LE_ADVERTISING_MANAGER_IFACE

# D-Bus interface constants
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
PROP_IFACE = "org.freedesktop.DBus.Properties"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"

# -------------------------------------------------
# GATT Application, Service, Characteristic
# -------------------------------------------------


class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = "/"
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        response = {}
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            for chrc in service.characteristics:
                response[chrc.get_path()] = chrc.get_properties()
        return response


class Service(dbus.service.Object):
    def __init__(self, bus, index, uuid, primary=True):
        self.path = f"/com/example/service{index}"
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, chrc):
        self.characteristics.append(chrc)

    def get_properties(self):
        return {
            GATT_SERVICE_IFACE: {
                "UUID": self.uuid,
                "Primary": self.primary,
            }
        }


class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = service.path + f"/char{index}"
        self.bus = bus
        self.uuid = uuid
        self.flags = flags
        self.service = service
        self.notifying = False
        self.value = [dbus.Byte(0)]
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def get_properties(self):
        return {
            GATT_CHRC_IFACE: {
                "Service": self.service.get_path(),
                "UUID": self.uuid,
                "Flags": self.flags,
            }
        }

    def _notify_value(self, text: str):
        if not self.notifying:
            return
        data = [dbus.Byte(b) for b in text.encode("utf-8")]
        self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": data}, [])

    def _process_ain_command(self, mode_type, target_mode):
        """Helper method to process AIN commands"""
        try:
            channel = None

            if target_mode == 195:
                # Mode 195: Only A channel available
                channel = "A"
                print(f"📌 Mode 195: Setting AIN A to {mode_type}")

            elif target_mode == 196:
                # Mode 196: Both channels available
                # Track last channel used
                if not hasattr(self, 'last_ain_channel'):
                    self.last_ain_channel = "A"  # Default to A first

                # Toggle between A and B
                channel = self.last_ain_channel
                self.last_ain_channel = "B" if self.last_ain_channel == "A" else "A"
                print(f"📌 Mode 196: Setting AIN {channel} to {mode_type}")

            else:
                print(f"❌ Unknown target mode: {target_mode}")
                return

            # Set lock for AIN command
            self.write_lock.set()

            def execute_ain_command():
                try:
                    if channel is None:
                        print("❌ No channel assigned for this mode")
                        return

                    success = self.pam_controller.change_pam_ain_mode(
                        mode_type[0], channel)
                    result = f"AIN{channel} set to {mode_type[0]}: {'✅ SUCCESS' if success else '❌ FAILED'}"
                    print(f"✅ {result}")

                except Exception as e:
                    print(f"❌ AIN command execution error: {e}")
                finally:
                    self.write_lock.clear()

            threading.Thread(target=execute_ain_command, daemon=True).start()

        except Exception as e:
            print(f"❌ Error in _process_ain_command: {e}")
            self.write_lock.clear()

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self):
        self.notifying = True

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self):
        self.notifying = False

    # D-Bus method overrides
    @dbus.service.method(PROP_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        props = self.get_properties().get(interface, {})
        if prop not in props:
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.InvalidArgs", "No such property")
        return props[prop]

    @dbus.service.method(PROP_IFACE, in_signature="ssv")
    def Set(self, interface, prop, value):
        raise dbus.exceptions.DBusException(
            "org.freedesktop.DBus.Error.NotSupported", "Not supported")

    @dbus.service.method(PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})

    @dbus.service.signal(PROP_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        return dbus.Array(self.value, signature="y")

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        """Handle write requests from BLE clients with mode-based constraints."""
        try:
            # Convert bytes to string and clean
            received = bytes(value).decode('utf-8').strip().upper()
            print(f"BLE Received: '{received}'")

            # Quick validation
            if not received or not hasattr(self, 'pam_controller') or not self.pam_controller:
                print("❌ PAM controller not available")
                return

            # Command mappings
            COMMANDS = {
                # Mode changes
                "195": ("change_mode", 195),
                "196": ("change_mode", 196),

                # AIN mode settings - mobile only sends VOLTAGE or CURRENT
                "VOLTAGE": ("set_ain_mode", "Voltage"),
                "CURRENT": ("set_ain_mode", "Current"),

                # Current status requests
                "CUR": ("set_current", "A"),
                "CURA": ("set_current", "A"),
                "CURB": ("set_current", "B"),
            }
            cmd_parts = received.split(':')
            base_cmd = cmd_parts[0]
            cmd_value = cmd_parts[1] if len(cmd_parts) > 1 else None
            fun = cmd_parts[2] if len(cmd_parts) > 2 else None

            # Check if command exists
            if base_cmd not in COMMANDS:
                print(f"❌ Unknown command: {received}")
                return

            cmd_info = COMMANDS[base_cmd]
            cmd_type = cmd_info[0]

            # Initialize variables
            mode_type = None
            target_mode = None

            # Check if this is a mode change command
            if cmd_type == "change_mode":
                target_mode = cmd_info[1]

                print(f"📌 Mode change command received: {target_mode}")
                # Set lock to pause main loop
                self.write_lock.set()

                def execute_mode_command():
                    try:
                        # Execute mode change first
                        success = self.pam_controller.change_pam_function(
                            target_mode)
                        result = f"Mode {target_mode}: {'✅ SUCCESS' if success else '❌ FAILED'}"
                        print(f"✅ {result}")

                        if success:
                            # Store the successful mode
                            self.last_mode_command = target_mode

                            # Reset channel toggle for mode 196
                            if target_mode == 196:
                                self.last_ain_channel = "A"

                            # Process any pending AIN commands that were queued
                            if hasattr(self, 'ain_command_queue') and self.ain_command_queue:
                                print(
                                    f"📌 Processing {len(self.ain_command_queue)} pending AIN commands...")
                                for pending_cmd in self.ain_command_queue:
                                    # Pass the actual mode that was just set
                                    self._process_ain_command(pending_cmd['mode_type'],
                                                              self.last_mode_command)  # FIXED: Use last_mode_command
                                # Clear the queue after processing
                                self.ain_command_queue = []
                        else:
                            # Mode change failed, clear any pending commands
                            if hasattr(self, 'ain_command_queue'):
                                print(
                                    "❌ Mode change failed, clearing AIN command queue")
                                self.ain_command_queue = []

                    except Exception as e:
                        print(f"❌ Command execution error: {e}")
                    finally:
                        self.write_lock.clear()

                # Run mode command in thread
                threading.Thread(target=execute_mode_command,
                                 daemon=True).start()

            elif cmd_type == "set_ain_mode":
                mode_type = cmd_info[1]

                # Check if we have a successful mode change yet
                if not hasattr(self, 'last_mode_command'):
                    print(
                        "⚠️ No mode set yet. Queueing AIN command for after mode change...")

                    # Initialize queue if needed
                    if not hasattr(self, 'ain_command_queue'):
                        self.ain_command_queue = []

                    # Queue this AIN command - store the mode_type only
                    self.ain_command_queue.append({
                        'mode_type': mode_type
                        # No target_mode here - will use the mode after change
                    })
                    return

                # Process AIN command immediately if mode is already set
                target_mode = self.last_mode_command
                self._process_ain_command(mode_type, target_mode)

            elif cmd_type == "set_current":
                channel = cmd_info[1]

                # Check if we have a value
                if cmd_value is None:
                    print("❌ No current value provided")
                    return

                try:
                    value = int(float(cmd_value))  # Handle both int and float

                    # Optional: Add range validation
                    if value < 0 or value > 5000:  # Adjust range as needed
                        print(f"❌ Current value {value} out of range")
                        return

                    # Set lock for current command
                    self.write_lock.set()

                    def execute_current_command():
                        try:
                            success = self.pam_controller.set_current_value(
                                value, channel, fun)
                            result = f"SET CUR{channel}={value}: {'✅' if success else '❌'}"
                            print(f"✅ {result}")

                            # Optional: Read back and confirm
                            if success:
                                time.sleep(0.1)
                                read_value = self.pam_controller.get_current_status(
                                    channel)
                                print(f"📊 Read back: {read_value}")

                        except Exception as e:
                            print(f"❌ Current command error: {e}")
                        finally:
                            self.write_lock.clear()

                    threading.Thread(
                        target=execute_current_command, daemon=True).start()

                except ValueError:
                    print(f"❌ Invalid current value: {cmd_value}")

        except Exception as e:
            print(f"❌ BLE Write error: {e}")
            if hasattr(self, 'write_lock'):
                self.write_lock.clear()


class DataCharacteristic(Characteristic):
    """Characteristic that sends notifications with machine state."""

    def __init__(self, bus, index, service, state, pam_controller=None, write_lock=None):
        super().__init__(bus, index, CHAR_UUID, [
            "read", "notify", "write"], service)
        self.state = state   # MachineState instance
        self.pam_controller = pam_controller
        self.write_lock = write_lock

    def start_sending(self):
        """Background thread: read state and notify every 0.2s."""
        def loop():
            while True:
                try:
                    data = self.state.get_all()
                    packet = (
                        f"FUNC:{data['FUNC']},"
                        f"WA:{data['WA']},"
                        f"WB:{data['WB']},"
                        f"IA:{data['IA']},"
                        f"IB:{data['IB']},"
                        f"MODE:{data['MODE']},"
                        f"READY:{data['READY']},"
                        f"PIN15:{data['PIN15']},"
                        f"PIN6:{data['PIN6']},"
                        f"ENABLED_B:{data['ENABLED_B']},"
                        f"CURRENT_A_STATUS:{data['CURRENT_A_STATUS']},"
                        f"CURRENT_B_STATUS:{data['CURRENT_B_STATUS']},"
                        f"CURRENT_STATUS:{data['CURRENT_STATUS']}\n"
                    )
                    self.value = [dbus.Byte(b) for b in packet.encode("utf-8")]
                    self._notify_value(packet)
                except Exception as e:
                    print("BLE notification error:", e)
                time.sleep(0.2)

        threading.Thread(target=loop, daemon=True).start()


# -------------------------------------------------
# Advertisement
# -------------------------------------------------
class Advertisement(dbus.service.Object):
    def __init__(self, bus, index, adapter_path):
        self.path = f"/com/example/advertisement{index}"
        self.bus = bus
        self.adapter_path = adapter_path
        self.service_uuids = [SERVICE_UUID]
        self.local_name = BLE_DEVICE_NAME
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def get_properties(self):
        return {
            LE_ADVERTISEMENT_IFACE: {
                "Type": "peripheral",
                "ServiceUUIDs": dbus.Array(self.service_uuids, signature="s"),
                "LocalName": self.local_name,
                "IncludeTxPower": True,
            }
        }

    @dbus.service.method(PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != LE_ADVERTISEMENT_IFACE:
            return {}
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE)
    def Release(self):
        pass


def unregister_old_advertisement(bus, adapter_path, adv_path):
    """Try to unregister an advertisement by path if it exists."""
    try:
        ad_manager = dbus.Interface(
            bus.get_object("org.bluez", adapter_path),
            LE_ADVERTISING_MANAGER_IFACE
        )
        ad_manager.UnregisterAdvertisement(dbus.ObjectPath(adv_path))
        print(f"✅ Unregistered old advertisement: {adv_path}")
    except dbus.exceptions.DBusException as e:
        # Ignore error if it wasn't registered
        if "org.bluez.Error.DoesNotExist" not in str(e) and \
           "org.bluez.Error.NotPermitted" not in str(e):
            print(f"⚠️ Failed to unregister {adv_path}: {e}")

# -------------------------------------------------
# Main entry: start BLE server in a GLib main loop thread
# -------------------------------------------------


def run_ble_server(state, pam_controller, write_lock):
    """Set up and register GATT application and advertisement.
       This function will block; call it in a separate thread."""
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    adapter = find_adapter(bus)
    if not adapter:
        print("❌ No BLE adapter found (needs LEAdvertisingManager1 + GattManager1)")
        return

    unregister_old_advertisement(bus, adapter, "/com/example/advertisement0")
    # Build GATT app
    app = Application(bus)
    service = Service(bus, 0, SERVICE_UUID, True)
    ch = DataCharacteristic(bus, 0, service, state, pam_controller, write_lock)
    service.add_characteristic(ch)
    app.add_service(service)

    # Register GATT app
    service_manager = dbus.Interface(
        bus.get_object("org.bluez", adapter), GATT_MANAGER_IFACE
    )
    ad_manager = dbus.Interface(
        bus.get_object("org.bluez", adapter), LE_ADVERTISING_MANAGER_IFACE
    )

    adv = Advertisement(bus, 0, adapter)

    mainloop = GLib.MainLoop()

    def on_app_registered():
        print("✅ GATT application registered")
        ch.start_sending()

    def on_app_error(e):
        print("❌ Failed to register application:", e)
        mainloop.quit()

    def on_adv_registered():
        print(f"✅ Advertisement registered: name={BLE_DEVICE_NAME}")

    def on_adv_error(e):
        print("❌ Failed to register advertisement:", e)
        mainloop.quit()

    service_manager.RegisterApplication(
        app.get_path(),
        {},
        reply_handler=on_app_registered,
        error_handler=on_app_error
    )

    ad_manager.RegisterAdvertisement(
        adv.get_path(),
        {},
        reply_handler=on_adv_registered,
        error_handler=on_adv_error
    )

    try:
        mainloop.run()
    finally:
        try:
            ad_manager.UnregisterAdvertisement(adv.get_path())
        except:
            pass
