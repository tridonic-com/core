"""Test the Tridonic DALI sensor platform (identification and luminaire)."""

from unittest.mock import patch

from dali.gear.sequences import QueryEmergencyModeResponse, QueryEmergencyStatusResponse
import pytest

from homeassistant.components.dali.const import DOMAIN, registry_identifier
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    DaliEmergencyData,
    DaliGearData,
)
from homeassistant.components.dali.sensor import (
    EMERGENCY_SENSOR_TYPES,
    DaliEmergencySensor,
    _emergency_mode,
)
from homeassistant.const import CONF_DEVICE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import FakeDaliDriver, decode_status

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


def _sensor(
    coordinator: DaliCoordinator, key: str, address: int = 1
) -> DaliEmergencySensor:
    """Return the emergency sensor entity for a description key."""
    description = next(d for d in EMERGENCY_SENSOR_TYPES if d.key == key)
    return DaliEmergencySensor(coordinator, "entry", "USB-DALI2", address, description)


@pytest.mark.parametrize(
    ("mode", "status", "expected"),
    [
        (
            decode_status(QueryEmergencyModeResponse, 0b00000010),
            decode_status(QueryEmergencyStatusResponse, 0),
            "normal",
        ),
        (
            decode_status(QueryEmergencyModeResponse, 0b00000100),
            decode_status(QueryEmergencyStatusResponse, 0),
            "emergency",
        ),
        (
            decode_status(QueryEmergencyModeResponse, 0b00001000),
            decode_status(QueryEmergencyStatusResponse, 0),
            "extended_emergency",
        ),
        (
            decode_status(QueryEmergencyModeResponse, 0b00000001),
            decode_status(QueryEmergencyStatusResponse, 0),
            "rest",
        ),
        (
            decode_status(QueryEmergencyModeResponse, 0b00010000),
            decode_status(QueryEmergencyStatusResponse, 0),
            "function_test",
        ),
        (
            decode_status(QueryEmergencyModeResponse, 0b00100000),
            decode_status(QueryEmergencyStatusResponse, 0),
            "duration_test",
        ),
        (
            decode_status(QueryEmergencyModeResponse, 0b00000010),
            decode_status(QueryEmergencyStatusResponse, 0b00000001),
            "inhibit",
        ),
        (None, None, None),
    ],
    ids=[
        "normal",
        "emergency",
        "extended_emergency",
        "rest",
        "function_test",
        "duration_test",
        "inhibit",
        "no_data",
    ],
)
def test_emergency_mode_decoding(
    mode: QueryEmergencyModeResponse | None,
    status: QueryEmergencyStatusResponse | None,
    expected: str | None,
) -> None:
    """Test the operating mode is derived from the mode and status bytes."""
    data = DaliEmergencyData(mode=mode, status=status)
    assert _emergency_mode(data) == expected


@pytest.mark.parametrize(
    ("key", "field", "value", "expected"),
    [
        ("battery_charge", "battery_charge", 254, 100),
        ("battery_charge", "battery_charge", 127, 50),
        ("duration_test_result", "duration_test_result", 60, 120),
        ("rated_duration", "rated_duration", 90, 180),
        ("lamp_total_operation_time", "lamp_total_operation_time", 100, 400),
        ("lamp_emergency_time", "lamp_emergency_time", 5, 5),
        ("emergency_level", "emergency_level", 254, 254),
    ],
    ids=[
        "battery_full",
        "battery_half",
        "duration_test_result_x2",
        "rated_duration_x2",
        "lamp_total_operation_x4",
        "lamp_emergency_time_direct",
        "emergency_level_direct",
    ],
)
def test_emergency_sensor_native_value_scaling(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    key: str,
    field: str,
    value: int,
    expected: int,
) -> None:
    """Test the measurement sensors scale the raw gear values correctly."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        1: DaliGearData(
            device_types=(1,), emergency=DaliEmergencyData(**{field: value})
        )
    }
    sensor = _sensor(coordinator, key)

    assert sensor.available
    assert sensor.native_value == expected


def test_emergency_sensor_value_none_when_field_absent(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a scaled sensor returns None when the gear does not report the value."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        1: DaliGearData(device_types=(1,), emergency=DaliEmergencyData())
    }

    assert _sensor(coordinator, "duration_test_result").native_value is None
    assert _sensor(coordinator, "battery_charge").native_value is None


def test_emergency_sensor_unavailable_without_data(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the sensor is unavailable when the gear has no emergency data."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {1: DaliGearData(device_types=(1,), emergency=None)}
    sensor = _sensor(coordinator, "battery_charge")

    assert not sensor.available
    assert sensor.native_value is None


async def test_emergency_sensor_platform_creates_entities(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the sensor platform creates emergency sensors for a DT1 gear."""
    await _setup(hass, fake_dali_driver)

    mode_state = hass.states.get("sensor.usb_dali2_1_gear_emergency_mode")
    assert mode_state is not None
    assert mode_state.state == "normal"

    battery_state = hass.states.get("sensor.usb_dali2_1_gear_battery")
    assert battery_state is not None
    assert battery_state.state == "100"


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
