# ble/bluez_helpers.py
import dbus

BLUEZ_SERVICE_NAME = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"

def find_adapter(bus):
    """Return the D-Bus path of the first BLE adapter with both GATT and LE Advertising managers."""
    om = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
    objects = om.GetManagedObjects()
    for path, ifaces in objects.items():
        if (LE_ADVERTISING_MANAGER_IFACE in ifaces and
                GATT_MANAGER_IFACE in ifaces):
            return path
    return None