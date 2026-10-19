"""Repairs for the Tridonic DALI integration."""

import probatio

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant

from . import DaliConfigEntry
from .const import CONF_BUS_POWER


class EnableBusPowerRepairFlow(RepairsFlow):
    """Switch on the interface's integrated bus power supply."""

    def __init__(self, entry: DaliConfigEntry) -> None:
        """Initialize the flow."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Handle the first step of the fix flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Ask the user to confirm enabling bus power, then reload."""
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_BUS_POWER: True},
            )
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=probatio.Schema({}),
            description_placeholders={"title": self._entry.title},
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a DALI repair issue."""
    assert data is not None
    entry_id = data["entry_id"]
    assert isinstance(entry_id, str)
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    return EnableBusPowerRepairFlow(entry)
