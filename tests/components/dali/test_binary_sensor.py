"""Test the Tridonic DALI binary sensor platform (DT1 emergency bits)."""

from unittest.mock import patch

from dali.gear.sequences import (
    QueryEmergencyFailureStatusResponse,
    QueryEmergencyModeResponse,
    QueryEmergencyStatusResponse,
)
import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.dali.binary_sensor import (
    EMERGENCY_BINARY_SENSOR_TYPES,
    DaliEmergencyBinarySensor,
    _status_bit,
)
from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    DaliEmergencyData,
    DaliGearData,
)
from homeassistant.const import CONF_DEVICE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import FakeDaliDriver, decode_status

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
    ("byte", "attr", "expected"),
    [
        (
            decode_status(QueryEmergencyStatusResponse, 0b00001000),
            "battery_fully_charged",
            True,
        ),
        (
            decode_status(QueryEmergencyStatusResponse, 0b00000000),
            "battery_fully_charged",
            False,
        ),
        (None, "battery_fully_charged", None),
    ],
    ids=["set", "clear", "no_byte"],
)
def test_status_bit_helper(
    byte: QueryEmergencyStatusResponse | None, attr: str, expected: bool | None
) -> None:
    """Test the status-bit helper reports set, clear and absent bytes."""
    value_fn = _status_bit(lambda data: byte, attr)
    assert value_fn(DaliEmergencyData()) is expected


@pytest.mark.parametrize(
    ("key", "emergency", "expected"),
    [
        (
            "battery_fully_charged",
            DaliEmergencyData(
                status=decode_status(QueryEmergencyStatusResponse, 0b00001000)
            ),
            True,
        ),
        (
            "inhibit_mode",
            DaliEmergencyData(
                status=decode_status(QueryEmergencyStatusResponse, 0b00001000)
            ),
            False,
        ),
        (
            "function_test_done",
            DaliEmergencyData(
                status=decode_status(QueryEmergencyStatusResponse, 0b00000010)
            ),
            True,
        ),
        (
            "hardwired_switch_on",
            DaliEmergencyData(
                mode=decode_status(QueryEmergencyModeResponse, 0b10000000)
            ),
            True,
        ),
        (
            "hardwired_inhibit_active",
            DaliEmergencyData(
                mode=decode_status(QueryEmergencyModeResponse, 0b00000010)
            ),
            False,
        ),
        (
            "circuit_failure",
            DaliEmergencyData(
                failure_status=decode_status(
                    QueryEmergencyFailureStatusResponse, 0b00000001
                )
            ),
            True,
        ),
        (
            "duration_test_failed",
            DaliEmergencyData(
                failure_status=decode_status(
                    QueryEmergencyFailureStatusResponse, 0b10000000
                )
            ),
            True,
        ),
        (
            "battery_failure",
            DaliEmergencyData(
                failure_status=decode_status(
                    QueryEmergencyFailureStatusResponse, 0b00000000
                )
            ),
            False,
        ),
    ],
)
def test_emergency_binary_sensor_is_on(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    key: str,
    emergency: DaliEmergencyData,
    expected: bool,
) -> None:
    """Test each bit entity reflects the right bit of the right status byte."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {1: DaliGearData(device_types=(1,), emergency=emergency)}
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
        hass.states.get("binary_sensor.usb_dali2_1_gear_control_gear_failure") is None
    )
    assert (
        hass.states.get("binary_sensor.usb_dali2_1_gear_light_source_failure") is None
    )
