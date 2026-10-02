"""Test the Tridonic DALI binary sensor platform (DT1 emergency bits)."""

from unittest.mock import patch

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.dali.binary_sensor import (
    EMERGENCY_BINARY_SENSOR_TYPES,
    DaliEmergencyBinarySensor,
    _bit,
)
from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    DaliEmergencyData,
    DaliGearData,
)
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry


def _binary_sensor(
    coordinator: DaliCoordinator, key: str, address: int = 1
) -> DaliEmergencyBinarySensor:
    """Return the emergency binary sensor entity for a description key."""
    description = next(d for d in EMERGENCY_BINARY_SENSOR_TYPES if d.key == key)
    return DaliEmergencyBinarySensor(
        coordinator, "entry", "USB-DALI2", address, description
    )


@pytest.mark.parametrize(
    ("byte", "mask", "expected"),
    [
        (0b00001000, 0b00001000, True),
        (0b00000000, 0b00001000, False),
        (None, 0b1, None),
    ],
    ids=["set", "clear", "no_byte"],
)
def test_bit_helper(byte: int | None, mask: int, expected: bool | None) -> None:
    """Test the bit helper reports set, clear and absent bytes."""
    assert _bit(byte, mask) is expected


@pytest.mark.parametrize(
    ("key", "field", "value", "expected"),
    [
        ("battery_fully_charged", "status", 0b00001000, True),
        ("inhibit_mode", "status", 0b00001000, False),
        ("function_test_done", "status", 0b00000010, True),
        ("hardwired_switch_on", "mode", 0b10000000, True),
        ("hardwired_inhibit_active", "mode", 0b00000010, False),
        ("circuit_failure", "failure_status", 0b00000001, True),
        ("duration_test_failed", "failure_status", 0b10000000, True),
        ("battery_failure", "failure_status", 0b00000000, False),
    ],
)
def test_emergency_binary_sensor_is_on(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    key: str,
    field: str,
    value: int,
    expected: bool,
) -> None:
    """Test each bit entity reflects the right bit of the right status byte."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        1: DaliGearData(
            device_types=(1,), emergency=DaliEmergencyData(**{field: value})
        )
    }
    sensor = _binary_sensor(coordinator, key)

    assert sensor.available
    assert sensor.is_on is expected


def test_failure_bits_use_problem_device_class() -> None:
    """Test every failure bit is reported as a problem."""
    failure_keys = {
        "circuit_failure",
        "battery_duration_failure",
        "battery_failure",
        "emergency_lamp_failure",
        "function_test_max_delay_exceeded",
        "duration_test_max_delay_exceeded",
        "function_test_failed",
        "duration_test_failed",
    }
    for description in EMERGENCY_BINARY_SENSOR_TYPES:
        if description.key in failure_keys:
            assert description.device_class is BinarySensorDeviceClass.PROBLEM


def test_emergency_binary_sensor_unavailable_without_data(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the bit entity is unavailable when the gear has no emergency data."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {1: DaliGearData(device_types=(1,), emergency=None)}
    sensor = _binary_sensor(coordinator, "battery_fully_charged")

    assert not sensor.available
    assert sensor.is_on is None


async def test_emergency_binary_sensor_platform_creates_entities(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the binary sensor platform creates emergency bits for a DT1 gear."""
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

    charged = hass.states.get("binary_sensor.usb_dali2_1_gear_battery_fully_charged")
    assert charged is not None
    assert charged.state == "on"

    circuit = hass.states.get("binary_sensor.usb_dali2_1_gear_circuit_failure")
    assert circuit is not None
    assert circuit.state == "off"
