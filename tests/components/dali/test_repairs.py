"""Test the Tridonic DALI repairs."""

from unittest.mock import patch

from homeassistant.components.dali.const import (
    CONF_BUS_POWER,
    DOMAIN,
    bus_voltage_error_issue_id,
)
from homeassistant.components.repairs import DOMAIN as REPAIRS_DOMAIN
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry
from tests.components.repairs import process_repair_fix_flow, start_repair_fix_flow
from tests.typing import ClientSessionGenerator


async def test_bus_voltage_error_issue_is_fixed(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    issue_registry: ir.IssueRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test the repair enables bus power and reloads the entry."""
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyACM0", CONF_BUS_POWER: False},
        title="USB-DALI2",
    )
    entry.add_to_hass(hass)
    issue_id = bus_voltage_error_issue_id(entry.entry_id)
    with patch(
        "homeassistant.components.dali.DriverLubaRs232",
        return_value=fake_dali_driver,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="bus_voltage_error",
            translation_placeholders={"title": entry.title},
            data={"entry_id": entry.entry_id},
        )

        client = await hass_client()
        result = await start_repair_fix_flow(client, DOMAIN, issue_id)
        assert result["step_id"] == "confirm"

        result = await process_repair_fix_flow(client, result["flow_id"])
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_BUS_POWER] is True
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    entry.runtime_data.close()
