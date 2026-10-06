"""Test the Tridonic DALI sensor platform (DT1 emergency sensors)."""

from unittest.mock import patch

import pytest

from homeassistant.components.dali.const import DOMAIN
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
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry


def _sensor(
    coordinator: DaliCoordinator, key: str, address: int = 1
) -> DaliEmergencySensor:
    """Return the emergency sensor entity for a description key."""
    description = next(d for d in EMERGENCY_SENSOR_TYPES if d.key == key)
    return DaliEmergencySensor(coordinator, "entry", "USB-DALI2", address, description)


@pytest.mark.parametrize(
    ("mode", "status", "expected"),
    [
        (0b00000010, 0b00000000, "normal"),
        (0b00000100, 0b00000000, "emergency"),
        (0b00001000, 0b00000000, "extended_emergency"),
        (0b00000001, 0b00000000, "rest"),
        (0b00010000, 0b00000000, "function_test"),
        (0b00100000, 0b00000000, "duration_test"),
        (0b00000010, 0b00000001, "inhibit"),
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
    mode: int | None, status: int | None, expected: str | None
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

    mode_state = hass.states.get("sensor.usb_dali2_1_gear_emergency_mode")
    assert mode_state is not None
    assert mode_state.state == "normal"

    battery_state = hass.states.get("sensor.usb_dali2_1_gear_battery")
    assert battery_state is not None
    assert battery_state.state == "100"


@pytest.mark.parametrize(
    "entity_id",
    [
        "sensor.usb_dali2_0_gear_batch_number",
        "sensor.usb_dali2_1_sensor_batch_number",
    ],
    ids=["control_gear", "control_device"],
)
async def test_batch_number_sensor_reports_the_printed_number(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver, entity_id: str
) -> None:
    """Test the batch number is its own entity, as the registry has no field for it."""
    fake_dali_driver.dev_inst_map.mapping = {(1, 3): 6}
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
    assert state.state == "0000000000"
