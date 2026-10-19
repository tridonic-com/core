"""Test DALI-2 input-device entities."""

from unittest.mock import patch

from dali.address import DeviceShort
from dali.device.general_sensor import GeneralSensorEvent, QuantityName
from dali.device.light import LightEvent
from dali.device.occupancy import OccupancyEvent
from dali.device.pushbutton import ButtonPressed
import pytest

from homeassistant.components.dali.binary_sensor import DaliOccupancy
from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    DaliGearData,
    DaliGeneralSensorData,
)
from homeassistant.components.dali.event import DaliPushbuttonEvent
from homeassistant.components.dali.sensor import DaliIlluminance, DaliPart306Sensor
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_DEVICE, UnitOfSoundPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry


def _coordinator(hass: HomeAssistant, driver: FakeDaliDriver) -> DaliCoordinator:
    """Create a coordinator with a fake config entry."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, driver, entry)
    coordinator.data = {1: DaliGearData(level=128, device_types=(6,))}
    return coordinator


async def test_occupancy_event(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test occupancy events update the binary sensor."""
    coordinator = _coordinator(hass, fake_dali_driver)
    entity = DaliOccupancy(coordinator, "entry", "USB-DALI2", 1, 2)
    event = OccupancyEvent(
        short_address=DeviceShort(1),
        instance_number=2,
        data=OccupancyEvent.EventData(movement=True, occupied=True),
    )
    coordinator.event_data[(1, 2)] = event
    with patch.object(entity, "async_write_ha_state"):
        entity._handle_event(event)
    assert entity.is_on


async def test_illuminance_event(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test illuminance events update the sensor."""
    coordinator = _coordinator(hass, fake_dali_driver)
    entity = DaliIlluminance(coordinator, "entry", "USB-DALI2", 1, 2)
    event = LightEvent(short_address=DeviceShort(1), instance_number=2, data=512)
    coordinator.event_data[(1, 2)] = event
    with patch.object(entity, "async_write_ha_state"):
        entity._handle_event(event)
    assert entity.native_value == 512


async def test_pushbutton_event(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test pushbutton events trigger the event entity."""
    coordinator = _coordinator(hass, fake_dali_driver)
    entity = DaliPushbuttonEvent(coordinator, "entry", "USB-DALI2", 1, 2)
    event = ButtonPressed(short_address=DeviceShort(1), instance_number=2)
    coordinator.event_data[(1, 2)] = event
    with (
        patch.object(entity, "async_write_ha_state"),
        patch.object(entity, "_trigger_event") as trigger_event,
    ):
        entity._handle_event(event)
    trigger_event.assert_called_once_with("button_pressed")


async def test_part_306_temperature_event(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a Part 306 temperature sensor decodes measurement events."""
    coordinator = _coordinator(hass, fake_dali_driver)
    coordinator.input_devices[(1, 2)] = DaliGeneralSensorData(
        quantity_name=QuantityName.TEMPERATURE,
        unit_of_measurement=9,
        resolution=9,
        magnitude=127,
        input_signal_signed=False,
    )
    entity = DaliPart306Sensor(coordinator, "entry", "USB-DALI2", 1, 2)

    event = GeneralSensorEvent(short_address=DeviceShort(1), instance_number=2, data=26)
    coordinator.event_data[(1, 2)] = event
    with patch.object(entity, "async_write_ha_state"):
        entity._handle_event(event)

    assert entity.device_class is SensorDeviceClass.TEMPERATURE
    assert entity.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert entity.native_value == 26


async def test_part_306_sound_pressure_mapping(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a Part 306 sound-pressure sensor maps to the dB(A) device class."""
    coordinator = _coordinator(hass, fake_dali_driver)
    coordinator.input_devices[(1, 2)] = DaliGeneralSensorData(
        quantity_name=QuantityName.SOUND_PRESSURE_LEVEL,
        unit_of_measurement=55,
        resolution=9,
        magnitude=127,
        input_signal_signed=False,
    )
    entity = DaliPart306Sensor(coordinator, "entry", "USB-DALI2", 1, 2)

    assert entity.device_class is SensorDeviceClass.SOUND_PRESSURE
    assert entity.native_unit_of_measurement == UnitOfSoundPressure.WEIGHTED_DECIBEL_A


async def test_part_306_addressless_event_is_ignored(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test an Instance-scheme sensor event without a short address is ignored.

    An Instance-scheme event carries no short address, so it cannot be tied to
    a device; the handler must not raise when dereferencing it.
    """
    coordinator = _coordinator(hass, fake_dali_driver)
    coordinator.input_devices[(1, 2)] = DaliGeneralSensorData(
        quantity_name=QuantityName.TEMPERATURE,
        unit_of_measurement=9,
        resolution=9,
        magnitude=127,
        input_signal_signed=False,
    )
    entity = DaliPart306Sensor(coordinator, "entry", "USB-DALI2", 1, 2)

    event = GeneralSensorEvent(instance_number=2, data=26)
    assert event.short_address is None
    with patch.object(entity, "async_write_ha_state") as write_state:
        entity._handle_event(event)

    write_state.assert_not_called()


async def test_part_306_sensor_subscribes_only_after_added(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a sensor subscribes to events only once added to Home Assistant.

    Subscribing in __init__ would leave an orphaned listener on a
    constructed-but-unadded entity whose hass is still None, crashing
    async_write_ha_state when a matching event arrives.
    """
    coordinator = _coordinator(hass, fake_dali_driver)
    coordinator.input_devices[(1, 2)] = DaliGeneralSensorData(
        quantity_name=QuantityName.TEMPERATURE,
        unit_of_measurement=9,
        resolution=9,
        magnitude=127,
        input_signal_signed=False,
    )
    entity = DaliPart306Sensor(coordinator, "entry", "USB-DALI2", 1, 2)

    assert coordinator._event_listeners == set()

    await entity.async_added_to_hass()
    assert entity._handle_event in coordinator._event_listeners


async def test_part_306_unknown_quantity_is_generic(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test an unmapped Part 306 quantity becomes a plain measurement sensor."""
    coordinator = _coordinator(hass, fake_dali_driver)
    coordinator.input_devices[(1, 3)] = DaliGeneralSensorData(
        quantity_name=1,
        unit_of_measurement=53,
        resolution=9,
        magnitude=127,
        input_signal_signed=False,
    )
    entity = DaliPart306Sensor(coordinator, "entry", "USB-DALI2", 1, 3)

    assert entity.device_class is None
    assert entity.native_unit_of_measurement == "ppm"
    assert entity.native_value is None
    assert entity.entity_registry_enabled_default is False


async def test_part_306_sensor_without_unit(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test an unmapped Part 306 quantity without a reported unit has no unit."""
    coordinator = _coordinator(hass, fake_dali_driver)
    coordinator.input_devices[(1, 3)] = DaliGeneralSensorData(quantity_name=1)
    entity = DaliPart306Sensor(coordinator, "entry", "USB-DALI2", 1, 3)

    assert entity.native_unit_of_measurement is None


@pytest.mark.parametrize(
    "entity_class",
    [
        pytest.param(DaliOccupancy, id="occupancy"),
        pytest.param(DaliPushbuttonEvent, id="pushbutton"),
        pytest.param(DaliIlluminance, id="illuminance"),
    ],
)
@pytest.mark.parametrize("last_update_success", [True, False])
async def test_input_entity_availability(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    entity_class: type[DaliOccupancy | DaliPushbuttonEvent | DaliIlluminance],
    last_update_success: bool,
) -> None:
    """Test input-device entities follow the interface availability."""
    coordinator = _coordinator(hass, fake_dali_driver)
    coordinator.last_update_success = last_update_success
    entity = entity_class(coordinator, "entry", "USB-DALI2", 1, 2)

    assert entity.available is last_update_success


@pytest.mark.parametrize(
    ("data", "event_value", "expected"),
    [
        pytest.param(
            DaliGeneralSensorData(quantity_name=27),
            26,
            26,
            id="missing_scaling_metadata",
        ),
        pytest.param(
            DaliGeneralSensorData(quantity_name=27, resolution=0, magnitude=127),
            26,
            26,
            id="invalid_resolution",
        ),
        pytest.param(
            DaliGeneralSensorData(quantity_name=27, resolution=6, magnitude=127),
            82,
            10,
            id="low_resolution",
        ),
        pytest.param(
            DaliGeneralSensorData(quantity_name=27, resolution=12, magnitude=127),
            4,
            32,
            id="high_resolution",
        ),
        pytest.param(
            DaliGeneralSensorData(quantity_name=27, resolution=12, magnitude=127),
            511,
            4095,
            id="high_resolution_repeat_term",
        ),
        pytest.param(
            DaliGeneralSensorData(
                quantity_name=27,
                resolution=6,
                magnitude=127,
                input_signal_signed=True,
            ),
            82,
            -21,
            id="signed_input",
        ),
        pytest.param(
            DaliGeneralSensorData(quantity_name=27, resolution=9, magnitude=126),
            265,
            26.5,
            id="scaled_magnitude",
        ),
    ],
)
async def test_part_306_measurement_decoding(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    data: DaliGeneralSensorData,
    event_value: int,
    expected: float,
) -> None:
    """Test Part 306 event payloads are converted to measured values."""
    coordinator = _coordinator(hass, fake_dali_driver)
    coordinator.input_devices[(1, 4)] = data
    entity = DaliPart306Sensor(coordinator, "entry", "USB-DALI2", 1, 4)
    coordinator.event_data[(1, 4)] = GeneralSensorEvent(
        short_address=DeviceShort(1), instance_number=4, data=event_value
    )

    assert entity.native_value == expected


async def test_sensor_platform_creates_entities(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the sensor platform creates Part 304 and Part 306 entities."""
    fake_dali_driver.dev_inst_map.mapping = {(1, 2): 4, (1, 3): 6}
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

    assert hass.states.get("sensor.usb_dali2_1_sensor_illuminance") is not None
    assert hass.states.get("sensor.usb_dali2_1_sensor_temperature") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
