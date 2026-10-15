"""Test the Tridonic DALI repairs."""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from dali.exceptions import ProgramShortAddressFailure
from dali.memory import info
import pytest

from homeassistant.components.dali.const import (
    CONF_BUS_POWER,
    DOMAIN,
    address_collision_issue_id,
    bus_voltage_error_issue_id,
    unaddressed_control_device_issue_id,
    unaddressed_gear_issue_id,
)
from homeassistant.components.dali.coordinator import DaliDeviceData
from homeassistant.components.repairs import DOMAIN as REPAIRS_DOMAIN
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import FakeDaliDriver, _FakeGear

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


async def test_unaddressed_device_issue_is_fixed(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    issue_registry: ir.IssueRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test the repair assigns a device address and exposes the new sensor."""
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    fake_dali_driver.unaddressed_devices = [[(0, 6)]]
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

    issue_id = unaddressed_control_device_issue_id(entry.entry_id)
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable

    client = await hass_client()
    result = await start_repair_fix_flow(client, DOMAIN, issue_id)
    assert result["step_id"] == "confirm"
    result = await process_repair_fix_flow(client, result["flow_id"])
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    result = await process_repair_fix_flow(client, result["flow_id"])

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    assert (0, 0) in entry.runtime_data.input_devices
    assert hass.states.get("sensor.usb_dali2_0_sensor_temperature") is not None


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


async def _setup_entry(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> MockConfigEntry:
    """Set up a DALI entry with the fake driver and wait for the first scan."""
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
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


async def _run_collision_fix_flow(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: MockConfigEntry,
) -> dict[str, Any]:
    """Start the collision fix flow, confirm it, and return the final result."""
    client = await hass_client()
    result = await start_repair_fix_flow(
        client, DOMAIN, address_collision_issue_id(entry.entry_id)
    )
    assert result["step_id"] == "confirm"

    result = await process_repair_fix_flow(client, result["flow_id"])
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()

    return await process_repair_fix_flow(client, result["flow_id"])


async def test_address_collision_issue_is_fixed(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    issue_registry: ir.IssueRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test the repair restores the known gear's address and moves the intruder."""
    fake_dali_driver.gears[5] = (6,)
    fake_dali_driver.levels[5] = 128
    fake_dali_driver.gear_metadata[5] = {
        **fake_dali_driver.metadata,
        info.IdentificationNumber: 100,
    }
    fake_dali_driver.colliding[5] = [_FakeGear((6,), 200)]
    entry = await _setup_entry(hass, fake_dali_driver)

    # A colliding gear cannot be queried, so its identity is only known from a
    # scan before the collision appeared; model that persisted knowledge.
    entry.runtime_data._metadata[5] = DaliDeviceData(identification_number=100)

    issue_id = address_collision_issue_id(entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    result = await _run_collision_fix_flow(hass, hass_client, entry)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    coordinator = entry.runtime_data
    # The gear Home Assistant knew keeps address 5; the intruder takes a free one.
    assert coordinator._metadata[5].identification_number == 100
    intruder = next(
        address
        for address, data in coordinator._metadata.items()
        if data.identification_number == 200
    )
    assert intruder != 5
    assert intruder in coordinator.data


async def test_address_collision_unknown_identity_reassigns(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    issue_registry: ir.IssueRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test a collision with no recorded identity re-addresses both gear."""
    fake_dali_driver.colliding[5] = [_FakeGear((6,), 100), _FakeGear((6,), 200)]
    entry = await _setup_entry(hass, fake_dali_driver)

    issue_id = address_collision_issue_id(entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    result = await _run_collision_fix_flow(hass, hass_client, entry)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    coordinator = entry.runtime_data
    assert 5 not in coordinator.data
    serials = {data.identification_number for data in coordinator._metadata.values()}
    assert {100, 200} <= serials


@pytest.mark.parametrize(
    ("side_effect", "reason"),
    [
        pytest.param(OSError("offline"), "cannot_connect", id="offline"),
        pytest.param(ProgramShortAddressFailure(1), "program_failed", id="program"),
    ],
)
async def test_address_collision_fix_flow_failures(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    issue_registry: ir.IssueRegistry,
    fake_dali_driver: FakeDaliDriver,
    side_effect: Exception,
    reason: str,
) -> None:
    """Test re-addressing failures abort and keep the collision issue."""
    fake_dali_driver.gears[5] = (6,)
    fake_dali_driver.levels[5] = 128
    fake_dali_driver.gear_metadata[5] = {
        **fake_dali_driver.metadata,
        info.IdentificationNumber: 100,
    }
    fake_dali_driver.colliding[5] = [_FakeGear((6,), 200)]
    entry = await _setup_entry(hass, fake_dali_driver)

    with patch.object(fake_dali_driver, "_commission", side_effect=side_effect):
        result = await _run_collision_fix_flow(hass, hass_client, entry)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == reason
    assert (
        issue_registry.async_get_issue(
            DOMAIN, address_collision_issue_id(entry.entry_id)
        )
        is not None
    )


async def test_address_collision_fix_flow_unresolved(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    issue_registry: ir.IssueRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test a collision that persists after re-addressing aborts and stays raised."""
    fake_dali_driver.gears[5] = (6,)
    fake_dali_driver.levels[5] = 128
    fake_dali_driver.gear_metadata[5] = {
        **fake_dali_driver.metadata,
        info.IdentificationNumber: 100,
    }
    fake_dali_driver.colliding[5] = [_FakeGear((6,), 200)]
    entry = await _setup_entry(hass, fake_dali_driver)

    with patch.object(fake_dali_driver, "_set_short_address", return_value=None):
        result = await _run_collision_fix_flow(hass, hass_client, entry)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "collision_remaining"
    assert (
        issue_registry.async_get_issue(
            DOMAIN, address_collision_issue_id(entry.entry_id)
        )
        is not None
    )
