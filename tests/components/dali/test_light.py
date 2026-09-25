"""Test the Tridonic DALI light platform."""

from unittest.mock import AsyncMock, patch

from dali.gear.general import DAPC

from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import DaliCoordinator, DaliGearData
from homeassistant.components.dali.light import DaliLight
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry


async def test_light_commands_clamp_dali_mask(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test light commands never send DALI's mask value as brightness."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyACM0"},
        title="USB-DALI2",
    )
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        0: DaliGearData(level=128, min_level=86, linear_dimming=True, device_types=(6,))
    }
    light = DaliLight(coordinator, entry.entry_id, entry.title, 0)

    with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
        await light.async_turn_on(brightness=255)
    assert isinstance(fake_dali_driver.sent[-1], DAPC)
    assert fake_dali_driver.sent[-1].power == 254

    with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
        await light.async_turn_off()
    assert isinstance(fake_dali_driver.sent[-1], DAPC)
    assert fake_dali_driver.sent[-1].power == 0
