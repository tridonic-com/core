"""Constants for the Tridonic DALI integration."""

DOMAIN = "dali"

CONF_BUS_POWER = "bus_power"

ISSUE_UNADDRESSED_GEAR = "unaddressed_gear"
ISSUE_UNADDRESSED_CONTROLDEVICE = "unaddressed_controldevice"
ISSUE_BUS_VOLTAGE_ERROR = "bus_voltage_error"

# Registry-identifier infixes. Control gear and control devices have
# independent DALI short-address spaces, so the kind is encoded to keep a
# same-numbered gear and control device as distinct registry devices.
GEAR_ID_INFIX = "gear"
CONTROL_DEVICE_ID_INFIX = "controldevice"


def registry_identifier(entry_id: str, address: int, *, input_device: bool) -> str:
    """Return the device-registry identifier for a DALI gear or control device."""
    infix = CONTROL_DEVICE_ID_INFIX if input_device else GEAR_ID_INFIX
    return f"{entry_id}_{infix}_{address}"


def unaddressed_gear_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for unaddressed gear on a DALI bus."""
    return f"{ISSUE_UNADDRESSED_GEAR}_{entry_id}"


def unaddressed_control_device_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for unaddressed control devices on a DALI bus."""
    return f"{ISSUE_UNADDRESSED_CONTROLDEVICE}_{entry_id}"


def bus_voltage_error_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for an unpowered DALI bus."""
    return f"{ISSUE_BUS_VOLTAGE_ERROR}_{entry_id}"
