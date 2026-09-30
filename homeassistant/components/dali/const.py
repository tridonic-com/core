"""Constants for the Tridonic DALI integration."""

DOMAIN = "dali"

CONF_BUS_POWER = "bus_power"

ISSUE_UNADDRESSED_GEAR = "unaddressed_gear"
ISSUE_BUS_VOLTAGE_ERROR = "bus_voltage_error"


def unaddressed_gear_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for unaddressed gear on a DALI bus."""
    return f"{ISSUE_UNADDRESSED_GEAR}_{entry_id}"


def bus_voltage_error_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for an unpowered DALI bus."""
    return f"{ISSUE_BUS_VOLTAGE_ERROR}_{entry_id}"
