"""Test DALI-2 input-device entities."""

from unittest.mock import patch

from dali.address import DeviceShort
from dali.device.light import LightEvent
from dali.device.occupancy import OccupancyEvent
from dali.device.pushbutton import ButtonPressed

from homeassistant.components.dali.binary_sensor import DaliOccupancy
from homeassistant.components.dali.button import DaliPushbutton
from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import DaliCoordinator, DaliGearData
from homeassistant.components.dali.sensor import DaliIlluminance
from homeassistant.const import CONF_DEVICE
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
    entity = DaliOccupancy(coordinator, "entry", 1, 2)
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
    entity = DaliIlluminance(coordinator, "entry", 1, 2)
    event = LightEvent(short_address=DeviceShort(1), instance_number=2, data=512)
    coordinator.event_data[(1, 2)] = event
    with patch.object(entity, "async_write_ha_state"):
        entity._handle_event(event)
    assert entity.native_value == 512


async def test_pushbutton_event(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test pushbutton events update button attributes."""
    coordinator = _coordinator(hass, fake_dali_driver)
    entity = DaliPushbutton(coordinator, "entry", 1, 2)
    event = ButtonPressed(short_address=DeviceShort(1), instance_number=2)
    coordinator.event_data[(1, 2)] = event
    with patch.object(entity, "async_write_ha_state"):
        entity._handle_event(event)
    assert entity.extra_state_attributes == {"last_event": "ButtonPressed"}
