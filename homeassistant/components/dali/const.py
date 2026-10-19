"""Constants for the Tridonic DALI integration."""

DOMAIN = "dali"

CONF_BUS_POWER = "bus_power"
CONF_HAS_BUS_SUPPLY = "has_bus_power_supply"

# IEC 62386 control-gear device types the integration handles.
DEVICE_TYPE_LED = 6
SUPPORTED_GEAR_DEVICE_TYPES = (DEVICE_TYPE_LED,)

# Human-readable names for each control-gear device type. The IEC 62386 part
# specifying a device type DTn is part 2(01+n) (e.g. DT6 → IEC 62386-207).
GEAR_DEVICE_TYPE_NAMES: dict[int, str] = {
    DEVICE_TYPE_LED: "LED module",
}

ISSUE_UNADDRESSED_GEAR = "unaddressed_gear"
ISSUE_BUS_VOLTAGE_ERROR = "bus_voltage_error"
ISSUE_BUS_OVERLOAD = "bus_overload"
ISSUE_BUS_NO_SUPPLY = "bus_no_supply"
ISSUE_ADDRESS_COLLISION = "address_collision"

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


def bus_voltage_error_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for an unpowered DALI bus."""
    return f"{ISSUE_BUS_VOLTAGE_ERROR}_{entry_id}"


def bus_overload_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for an overloaded or undersized bus supply."""
    return f"{ISSUE_BUS_OVERLOAD}_{entry_id}"


def bus_no_supply_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for an interface with no bus power supply."""
    return f"{ISSUE_BUS_NO_SUPPLY}_{entry_id}"


def address_collision_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for a DALI short-address collision."""
    return f"{ISSUE_ADDRESS_COLLISION}_{entry_id}"
