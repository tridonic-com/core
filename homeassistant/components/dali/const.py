"""Constants for the Tridonic DALI integration."""

DOMAIN = "dali"

ISSUE_UNADDRESSED_GEAR = "unaddressed_gear"


def unaddressed_gear_issue_id(entry_id: str) -> str:
    """Return the repair issue ID for unaddressed gear on a DALI bus."""
    return f"{ISSUE_UNADDRESSED_GEAR}_{entry_id}"
