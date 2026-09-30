"""Test the Tridonic DALI integration setup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dali.exceptions import DALISequenceError, TransmissionError
import pytest

from homeassistant.components.dali import async_remove_config_entry_device
from homeassistant.components.dali.const import (
    CONF_BUS_POWER,
    DOMAIN,
    bus_voltage_error_issue_id,
)
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry


async def test_setup_and_unload_entry(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test setup scans and retains only DT6 and DT8 control gear."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyACM0"},
        title="USB-DALI2",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "homeassistant.components.dali.DriverLubaRs232",
            return_value=fake_dali_driver,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ) as forward_setups,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.runtime_data.data.keys() == {0, 2}
    assert entry.runtime_data.data[0].min_level == 86
    assert forward_setups.await_count == 1

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert fake_dali_driver.closed


async def test_setup_entry_with_no_supported_gear(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test setup succeeds when the DALI bus has no DT6 or DT8 gear."""
    fake_dali_driver.gears = {}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyACM0"},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "homeassistant.components.dali.DriverLubaRs232",
            return_value=fake_dali_driver,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.runtime_data.data == {}
    entry.runtime_data.close()


async def test_setup_entry_connection_failure(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test setup closes the driver when connection fails."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    entry.add_to_hass(hass)
    with (
        patch(
            "homeassistant.components.dali.DriverLubaRs232",
            return_value=fake_dali_driver,
        ),
        patch.object(fake_dali_driver, "connect", side_effect=OSError("offline")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)

    assert fake_dali_driver.closed


async def test_setup_bus_voltage_error_creates_repair(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test an unpowered bus raises a repair to switch on the interface supply."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0", CONF_BUS_POWER: False}
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "homeassistant.components.dali.DriverLubaRs232",
            return_value=fake_dali_driver,
        ),
        patch.object(fake_dali_driver, "connect", side_effect=TransmissionError(1)),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)

    assert fake_dali_driver.closed
    assert issue_registry.async_get_issue(
        DOMAIN, bus_voltage_error_issue_id(entry.entry_id)
    )


async def test_setup_bus_voltage_error_with_bus_power_no_repair(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test no repair is offered when the interface already powers the bus."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0", CONF_BUS_POWER: True}
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "homeassistant.components.dali.DriverLubaRs232",
            return_value=fake_dali_driver,
        ),
        patch.object(fake_dali_driver, "connect", side_effect=TransmissionError(1)),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)

    assert not issue_registry.async_get_issue(
        DOMAIN, bus_voltage_error_issue_id(entry.entry_id)
    )


async def test_setup_clears_stale_bus_voltage_repair(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test a successful setup removes a lingering bus-voltage repair."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0", CONF_BUS_POWER: False}
    )
    entry.add_to_hass(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        bus_voltage_error_issue_id(entry.entry_id),
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="bus_voltage_error",
        translation_placeholders={"title": "USB-DALI2"},
    )
    with (
        patch(
            "homeassistant.components.dali.DriverLubaRs232",
            return_value=fake_dali_driver,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert not issue_registry.async_get_issue(
        DOMAIN, bus_voltage_error_issue_id(entry.entry_id)
    )
    entry.runtime_data.close()


async def test_setup_entry_refresh_failure(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test setup closes the driver when the first refresh fails."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    entry.add_to_hass(hass)

    with (
        patch(
            "homeassistant.components.dali.DriverLubaRs232",
            return_value=fake_dali_driver,
        ),
        patch.object(fake_dali_driver, "send", side_effect=OSError("offline")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)

    assert fake_dali_driver.closed


async def test_setup_entry_dali_sequence_failure(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test setup ignores a gear when its device-type query fails."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    entry.add_to_hass(hass)
    with (
        patch(
            "homeassistant.components.dali.DriverLubaRs232",
            return_value=fake_dali_driver,
        ),
        patch.object(fake_dali_driver, "run_sequence", side_effect=DALISequenceError),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.runtime_data.data == {}
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("identifiers", "removable"),
    [
        pytest.param({(DOMAIN, "ENTRY_0")}, False, id="active_gear"),
        pytest.param({(DOMAIN, "ENTRY_1")}, False, id="active_input_device"),
        pytest.param({(DOMAIN, "ENTRY_5")}, True, id="stale_address"),
        pytest.param({("other", "ENTRY_0")}, True, id="foreign_identifier"),
    ],
)
async def test_remove_config_entry_device(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    identifiers: set[tuple[str, str]],
    removable: bool,
) -> None:
    """Test devices can only be removed once their address stops responding."""
    fake_dali_driver.dev_inst_map.mapping = {(1, 2): 4}
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"}, entry_id="ENTRY"
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "homeassistant.components.dali.DriverLubaRs232",
            return_value=fake_dali_driver,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    device_entry = SimpleNamespace(identifiers=identifiers)
    assert (
        await async_remove_config_entry_device(hass, entry, device_entry) is removable
    )

    entry.runtime_data.close()
