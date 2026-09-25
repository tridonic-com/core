"""Test the Tridonic DALI integration setup."""

from unittest.mock import AsyncMock, patch

from dali.exceptions import DALISequenceError

from homeassistant.components.dali.const import DOMAIN
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

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
