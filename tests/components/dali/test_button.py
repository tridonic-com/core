"""Test the Tridonic DALI button platform."""

from unittest.mock import patch

from dali.device.general import IdentifyDevice as IdentifyControlDevice
from dali.gear.general import IdentifyDevice
import pytest

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.dali.button import (
    EMERGENCY_BUTTON_TYPES,
    DaliDeviceIdentify,
    DaliEmergencyButton,
    DaliIdentify,
    DaliSearchDevices,
)
from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    DaliEmergencyData,
    DaliGearData,
)
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry


async def test_identify_press_sends_identify_command(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test pressing the identify button sends the DALI IdentifyDevice command."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {0: DaliGearData(level=128, device_types=(6,))}
    button = DaliIdentify(coordinator, entry.entry_id, entry.title, 0)

    assert button.available

    await button.async_press()

    assert isinstance(fake_dali_driver.sent[-1], IdentifyDevice)
    assert fake_dali_driver.sent[-1].destination.address == 0


async def test_identify_unavailable_when_gear_absent(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the identify button is unavailable when the gear is not responding."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {}
    button = DaliIdentify(coordinator, entry.entry_id, entry.title, 0)

    assert not button.available


async def test_identify_failure_raises_home_assistant_error(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test identify communication errors are surfaced as Home Assistant errors."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {0: DaliGearData(level=128, device_types=(6,))}
    button = DaliIdentify(coordinator, entry.entry_id, entry.title, 0)

    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("offline")),
        pytest.raises(HomeAssistantError),
    ):
        await button.async_press()


async def test_device_identify_press_sends_identify_command(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the control-device identify button sends the Part 103 command."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {}
    button = DaliDeviceIdentify(coordinator, entry.entry_id, entry.title, 5)

    await button.async_press()

    assert isinstance(fake_dali_driver.sent[-1], IdentifyControlDevice)
    assert fake_dali_driver.sent[-1].destination.address == 5


async def test_device_identify_setup_adds_button_per_device(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test one identify button is created per control-device address."""
    fake_dali_driver.dev_inst_map.mapping = {(5, 0): 1, (5, 1): 3, (6, 0): 4}
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.components.dali.DriverLubaRs232",
        return_value=fake_dali_driver,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    identify_entries = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if entity.domain == BUTTON_DOMAIN
    }
    assert f"{entry.entry_id}_5_controldevice_identify" in identify_entries
    assert f"{entry.entry_id}_6_controldevice_identify" in identify_entries


async def test_identify_setup_adds_button_per_gear(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test an identify button is created for each responding control gear."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.components.dali.DriverLubaRs232",
        return_value=fake_dali_driver,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    identify_entries = [
        entity
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if entity.domain == BUTTON_DOMAIN and entity.unique_id.endswith("_identify")
    ]
    assert {entity.unique_id for entity in identify_entries} == {
        f"{entry.entry_id}_0_identify",
        f"{entry.entry_id}_1_identify",
        f"{entry.entry_id}_2_identify",
    }


async def test_search_devices_press_runs_discovery(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test pressing the search button enumerates newly addressed devices."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {}
    button = DaliSearchDevices(coordinator, entry.entry_id, entry.title)
    fake_dali_driver.pending_device_instances = {(3, 0): 6}

    await button.async_press()

    assert (3, 0, 6) in coordinator.device_instances
    assert (3, 0) in coordinator.input_devices


EMERGENCY_BUTTON_COMMANDS = {
    description.key: description.command.__name__
    for description in EMERGENCY_BUTTON_TYPES
}


@pytest.mark.parametrize(
    ("key", "command_name"),
    list(EMERGENCY_BUTTON_COMMANDS.items()),
)
async def test_emergency_button_press_sends_command(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    key: str,
    command_name: str,
) -> None:
    """Test each emergency button issues its DT1 command via run_sequence."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        1: DaliGearData(device_types=(1,), emergency=DaliEmergencyData())
    }
    description = next(d for d in EMERGENCY_BUTTON_TYPES if d.key == key)
    button = DaliEmergencyButton(
        coordinator, entry.entry_id, entry.title, 1, description
    )

    assert button.available

    await button.async_press()

    assert (1, command_name) in fake_dali_driver.emergency_commands


async def test_emergency_button_failure_raises_home_assistant_error(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test emergency command errors are surfaced as Home Assistant errors."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        1: DaliGearData(device_types=(1,), emergency=DaliEmergencyData())
    }
    button = DaliEmergencyButton(
        coordinator, entry.entry_id, entry.title, 1, EMERGENCY_BUTTON_TYPES[0]
    )

    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("offline")),
        pytest.raises(HomeAssistantError),
    ):
        await button.async_press()


async def test_emergency_setup_adds_buttons_per_emergency_gear(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test the full set of emergency buttons is created for a DT1 gear."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.components.dali.DriverLubaRs232",
        return_value=fake_dali_driver,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    unique_ids = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if entity.domain == BUTTON_DOMAIN
    }
    for description in EMERGENCY_BUTTON_TYPES:
        assert f"{entry.entry_id}_1_{description.key}" in unique_ids
    # Non-emergency gear gets no emergency command buttons.
    assert f"{entry.entry_id}_0_start_function_test" not in unique_ids
