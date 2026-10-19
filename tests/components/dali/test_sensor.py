"""Test the Tridonic DALI sensor platform (identification and luminaire)."""

from unittest.mock import patch

import pytest

from homeassistant.components.dali.const import DOMAIN, registry_identifier
from homeassistant.const import CONF_DEVICE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry


async def _setup(hass: HomeAssistant, driver: FakeDaliDriver) -> MockConfigEntry:
    """Set up the integration against the fake driver."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"}, title="USB-DALI2"
    )
    entry.add_to_hass(hass)
    with patch("homeassistant.components.dali.DriverLubaRs232", return_value=driver):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_identification_sensor_reports_the_printed_number(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test the identification number entity reports the printed number.

    The device-info panel already carries this value as the device serial
    number, so the sensor itself is filed under diagnostics.
    """
    await _setup(hass, fake_dali_driver)

    entity_id = "sensor.usb_dali2_0_gear_identification_number"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "1"
    assert (
        entity_registry.async_get(entity_id).entity_category
        == EntityCategory.DIAGNOSTIC
    )


async def test_dali_address_sensor_reports_the_short_address(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test the DALI address entity reports the bus short address."""
    await _setup(hass, fake_dali_driver)

    entity_id = "sensor.usb_dali2_0_gear_dali_address"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "0"
    assert (
        entity_registry.async_get(entity_id).entity_category
        == EntityCategory.DIAGNOSTIC
    )


@pytest.mark.parametrize(
    ("entity_id", "expected_state", "expected_unit"),
    [
        ("sensor.usb_dali2_0_gear_luminaire_manufacturer_gtin", "4048879000000", None),
        ("sensor.usb_dali2_0_gear_luminaire_identification", "Office downlight", None),
        ("sensor.usb_dali2_0_gear_nominal_input_power", "42", "W"),
        ("sensor.usb_dali2_0_gear_nominal_light_output", "3600", "lm"),
        ("sensor.usb_dali2_0_gear_correlated_color_temperature", "4000", "K"),
        ("sensor.usb_dali2_0_gear_light_distribution_type", "type_iii", None),
        ("sensor.usb_dali2_0_gear_minimum_mains_voltage", "198", "V"),
    ],
    ids=[
        "gtin",
        "string",
        "power",
        "light_output",
        "cct",
        "enum",
        "voltage",
    ],
)
async def test_luminaire_sensors_report_the_decoded_values(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    entity_id: str,
    expected_state: str,
    expected_unit: str | None,
) -> None:
    """Test the memory bank 1 luminaire values reach the sensor states.

    The bank is read after setup has finished, so the entities only appear
    once the deferred read lands.
    """
    await _setup(hass, fake_dali_driver)

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected_state
    assert state.attributes.get("unit_of_measurement") == expected_unit


async def test_luminaire_sensors_are_diagnostic(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test every luminaire and identification sensor is filed under diagnostics."""
    entry = await _setup(hass, fake_dali_driver)

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, registry_identifier(entry.entry_id, 0, input_device=False)),
        entry.entry_id,
    )
    assert device is not None
    categories = {
        registry_entry.entity_category
        for registry_entry in er.async_entries_for_device(entity_registry, device.id)
        if registry_entry.domain == "sensor"
    }

    assert categories == {EntityCategory.DIAGNOSTIC}


async def test_gear_without_the_luminaire_bank_gets_no_luminaire_sensors(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test a gear that does not implement memory bank 1 gains no luminaire sensors."""
    await _setup(hass, fake_dali_driver)

    # Only gear 0 has a bank-1 entry in the fake.
    assert hass.states.get("sensor.usb_dali2_2_gear_nominal_input_power") is None
    assert hass.states.get("sensor.usb_dali2_2_gear_light_distribution_type") is None
    # The identification sensors do not depend on the luminaire bank.
    assert hass.states.get("sensor.usb_dali2_2_gear_identification_number") is not None


@pytest.mark.parametrize(
    ("entity_id", "expected_state", "expected_unit"),
    [
        ("sensor.usb_dali2_0_gear_active_energy", "1234.5", "Wh"),
        ("sensor.usb_dali2_0_gear_control_gear_operating_time", "360000", "s"),
        ("sensor.usb_dali2_0_gear_control_gear_supply_voltage", "230.5", "V"),
        ("sensor.usb_dali2_0_gear_control_gear_internal_temperature", "45", "°C"),
        ("sensor.usb_dali2_0_gear_light_source_current", "0.35", "A"),
        ("sensor.usb_dali2_0_gear_light_source_start_counter", "118", None),
        ("sensor.usb_dali2_0_gear_rated_median_useful_life", "50000", "h"),
        ("sensor.usb_dali2_0_gear_control_gear_reference_temperature", "65", "°C"),
    ],
    ids=[
        "bank_202_energy",
        "bank_205_duration",
        "bank_205_scaled_voltage",
        "bank_205_temperature",
        "bank_206_scaled_current",
        "bank_206_counter",
        "bank_207_life",
        "bank_207_temperature",
    ],
)
async def test_extended_bank_sensors_report_the_decoded_values(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    entity_id: str,
    expected_state: str,
    expected_unit: str | None,
) -> None:
    """Test the DiiA Part 252 and 253 banks reach the sensor states.

    The banks are read after setup has finished, so the entities only appear
    once the deferred diagnostics read lands.
    """
    await _setup(hass, fake_dali_driver)

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected_state
    assert state.attributes.get("unit_of_measurement") == expected_unit


async def test_gear_without_the_extended_banks_gets_no_sensors(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test a gear that implements none of the extended banks gains no entities."""
    await _setup(hass, fake_dali_driver)

    # Only gear 0 has the bank 202/205/206/207 entries in the fake.
    assert hass.states.get("sensor.usb_dali2_2_gear_active_energy") is None
    assert hass.states.get("sensor.usb_dali2_2_gear_light_source_voltage") is None
    assert hass.states.get("sensor.usb_dali2_2_gear_rated_median_useful_life") is None
