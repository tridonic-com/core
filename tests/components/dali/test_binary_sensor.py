"""Test the Tridonic DALI binary sensor platform (gear diagnostics)."""

from unittest.mock import patch

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.dali.const import DOMAIN
from homeassistant.const import CONF_DEVICE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry


@pytest.mark.parametrize(
    ("entity_id", "expected_state"),
    [
        ("binary_sensor.usb_dali2_0_gear_control_gear_thermal_derating", "on"),
        ("binary_sensor.usb_dali2_0_gear_control_gear_failure", "off"),
        ("binary_sensor.usb_dali2_0_gear_control_gear_supply_undervoltage", "off"),
        ("binary_sensor.usb_dali2_0_gear_light_source_failure", "off"),
        ("binary_sensor.usb_dali2_0_gear_light_source_open_circuit", "off"),
    ],
    ids=[
        "bank_205_set",
        "bank_205_clear",
        "bank_205_undervoltage",
        "bank_206_failure",
        "bank_206_open_circuit",
    ],
)
async def test_gear_diagnostic_binary_sensors_report_the_latched_flags(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    fake_dali_driver: FakeDaliDriver,
    entity_id: str,
    expected_state: str,
) -> None:
    """Test the DiiA Part 253 failure conditions reach the binary sensor states."""
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

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected_state
    assert state.attributes["device_class"] == BinarySensorDeviceClass.PROBLEM
    registry_entry = entity_registry.async_get(entity_id)
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_gear_without_the_diagnostic_banks_gets_no_binary_sensors(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a gear answering neither bank 205 nor 206 gains no entities."""
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

    assert (
        hass.states.get("binary_sensor.usb_dali2_2_gear_control_gear_failure") is None
    )
    assert (
        hass.states.get("binary_sensor.usb_dali2_2_gear_light_source_failure") is None
    )
