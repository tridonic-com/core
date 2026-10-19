"""Test DALI-2 input-device entities."""

from unittest.mock import patch

from dali.address import DeviceShort
from dali.device.occupancy import OccupancyEvent
from dali.device.pushbutton import ButtonPressed
import pytest

from homeassistant.components.dali.binary_sensor import DaliOccupancy
from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import DaliCoordinator, DaliGearData
from homeassistant.components.dali.event import DaliPushbuttonEvent
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


@pytest.mark.parametrize(
    "entity_class",
    [
        pytest.param(DaliOccupancy, id="occupancy"),
        pytest.param(DaliPushbuttonEvent, id="pushbutton"),
    ],
)
@pytest.mark.parametrize("last_update_success", [True, False])
async def test_input_entity_availability(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    entity_class: type[DaliOccupancy | DaliPushbuttonEvent],
    last_update_success: bool,
) -> None:
    """Test input-device entities follow the interface availability."""
    coordinator = _coordinator(hass, fake_dali_driver)
    coordinator.last_update_success = last_update_success
    entity = entity_class(coordinator, "entry", "USB-DALI2", 1, 2)

    assert entity.available is last_update_success
