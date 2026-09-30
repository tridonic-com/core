"""Test the Tridonic DALI repairs."""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from dali.exceptions import ProgramShortAddressFailure
import pytest

from homeassistant.components.dali.const import (
    CONF_BUS_POWER,
    DOMAIN,
    bus_voltage_error_issue_id,
    unaddressed_gear_issue_id,
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


@pytest.fixture
async def entry(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> MockConfigEntry:
    """Set up a DALI entry with one unaddressed DT6 gear on the bus."""
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    fake_dali_driver.unaddressed_gears = [(6,)]
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"}, title="USB-DALI2"
    )
    entry.add_to_hass(hass)
    with patch(
        "homeassistant.components.dali.DriverLubaRs232",
        return_value=fake_dali_driver,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _run_fix_flow(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: MockConfigEntry,
) -> dict[str, Any]:
    """Start the fix flow, confirm it, and return the final result."""
    client = await hass_client()
    result = await start_repair_fix_flow(
        client, DOMAIN, unaddressed_gear_issue_id(entry.entry_id)
    )
    assert result["step_id"] == "confirm"

    result = await process_repair_fix_flow(client, result["flow_id"])
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()

    return await process_repair_fix_flow(client, result["flow_id"])


async def test_unaddressed_gear_issue_is_fixed(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    issue_registry: ir.IssueRegistry,
    fake_dali_driver: FakeDaliDriver,
    entry: MockConfigEntry,
) -> None:
    """Test the repair assigns an address and exposes the new light."""
    issue_id = unaddressed_gear_issue_id(entry.entry_id)
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable

    result = await _run_fix_flow(hass, hass_client, entry)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    assert 3 in entry.runtime_data.data
    assert hass.states.get("light.usb_dali2_3_gear") is not None


@pytest.mark.parametrize(
    ("side_effect", "reason"),
    [
        pytest.param(OSError("offline"), "cannot_connect", id="offline"),
        pytest.param(ProgramShortAddressFailure(1), "program_failed", id="program"),
        pytest.param(lambda: None, "unaddressed_remaining", id="no_free_address"),
    ],
)
async def test_fix_flow_failures(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    issue_registry: ir.IssueRegistry,
    fake_dali_driver: FakeDaliDriver,
    entry: MockConfigEntry,
    side_effect: Exception | Callable[[], None],
    reason: str,
) -> None:
    """Test fix flow failures abort and keep the issue."""
    with patch.object(fake_dali_driver, "_commission", side_effect=side_effect):
        result = await _run_fix_flow(hass, hass_client, entry)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == reason
    assert (
        issue_registry.async_get_issue(
            DOMAIN, unaddressed_gear_issue_id(entry.entry_id)
        )
        is not None
    )


async def test_fix_flow_entry_not_loaded(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: MockConfigEntry,
) -> None:
    """Test the fix flow aborts when the entry is not loaded."""
    assert await hass.config_entries.async_unload(entry.entry_id)

    client = await hass_client()
    result = await start_repair_fix_flow(
        client, DOMAIN, unaddressed_gear_issue_id(entry.entry_id)
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


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


async def test_issue_removed_with_entry(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
    entry: MockConfigEntry,
) -> None:
    """Test removing the entry removes its repair issue."""
    assert await hass.config_entries.async_remove(entry.entry_id)

    assert (
        issue_registry.async_get_issue(
            DOMAIN, unaddressed_gear_issue_id(entry.entry_id)
        )
        is None
    )
