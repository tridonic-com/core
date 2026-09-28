"""Repairs for the Tridonic DALI integration."""

import asyncio

from dali.exceptions import ProgramShortAddressFailure
import probatio

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from . import DaliConfigEntry


class AssignShortAddressesRepairFlow(RepairsFlow):
    """Assign short addresses to unaddressed DALI control gear."""

    def __init__(self, entry: DaliConfigEntry) -> None:
        """Initialize the flow."""
        self._entry = entry
        self._task: asyncio.Task[bool] | None = None
        self._abort_reason: str | None = None

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Handle the first step of the fix flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Ask the user to confirm addressing."""
        if self._entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            return await self.async_step_assign()
        return self.async_show_form(
            step_id="confirm",
            data_schema=probatio.Schema({}),
            description_placeholders={"title": self._entry.title},
        )

    async def async_step_assign(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Run the commissioning sequence."""
        if self._task is None:
            self._task = self.hass.async_create_task(
                self._entry.runtime_data.async_assign_short_addresses()
            )
        if not self._task.done():
            return self.async_show_progress(
                step_id="assign",
                progress_action="assign",
                progress_task=self._task,
            )
        try:
            if self._task.result():
                self._abort_reason = "unaddressed_remaining"
        except OSError, TimeoutError:
            self._abort_reason = "cannot_connect"
        except ProgramShortAddressFailure:
            self._abort_reason = "program_failed"
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Finish the fix flow."""
        if self._abort_reason:
            return self.async_abort(reason=self._abort_reason)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for an unaddressed-gear issue."""
    assert data is not None
    entry_id = data["entry_id"]
    assert isinstance(entry_id, str)
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    return AssignShortAddressesRepairFlow(entry)
