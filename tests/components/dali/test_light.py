"""Test the Tridonic DALI light platform."""

from unittest.mock import AsyncMock, patch

from dali.address import GearShort
from dali.gear.general import DAPC
from dali.gear.sequences import QueryEmergencyFeaturesResponse
import pytest

from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    DaliEmergencyData,
    DaliGearData,
    _brightness_to_dali_level,
    _dali_level_to_brightness,
)
from homeassistant.components.dali.light import DaliLight, _is_light, _set_xy_color
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN, ColorMode
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import FakeDaliDriver, decode_status

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

    with patch.object(coordinator, "async_update_gear", new_callable=AsyncMock):
        await light.async_turn_on(brightness=255)
    assert isinstance(fake_dali_driver.sent[-1], DAPC)
    assert fake_dali_driver.sent[-1].power == 254

    with patch.object(coordinator, "async_update_gear", new_callable=AsyncMock):
        await light.async_turn_off()
    assert isinstance(fake_dali_driver.sent[-1], DAPC)
    assert fake_dali_driver.sent[-1].power == 0


async def test_light_command_failure_raises_home_assistant_error(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test DALI communication errors are surfaced as Home Assistant errors."""
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

    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("serial failure")),
        pytest.raises(HomeAssistantError),
    ):
        await light.async_turn_off()


async def test_light_properties(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test basic non-DT8 light properties."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {0: DaliGearData(level=128, min_level=86, device_types=(6,))}
    light = DaliLight(coordinator, entry.entry_id, entry.title, 0)

    assert light.available
    assert light.is_on
    assert light.brightness == 128
    assert light.color_mode is ColorMode.BRIGHTNESS
    assert light.color_temp_kelvin is None
    assert light.xy_color is None


@pytest.mark.parametrize(
    ("gear", "expected"),
    [
        (DaliGearData(device_types=(6,)), True),
        (DaliGearData(device_types=(8,)), True),
        (
            DaliGearData(
                device_types=(1,),
                emergency=DaliEmergencyData(
                    features=decode_status(QueryEmergencyFeaturesResponse, 0b00000010)
                ),
            ),
            True,
        ),
        (
            DaliGearData(
                device_types=(1,),
                emergency=DaliEmergencyData(
                    features=decode_status(QueryEmergencyFeaturesResponse, 0b00000001)
                ),
            ),
            False,
        ),
        (DaliGearData(device_types=(1,), emergency=None), False),
    ],
    ids=["dt6", "dt8", "dt1_maintained", "dt1_non_maintained", "dt1_no_data"],
)
def test_is_light_filters_non_maintained_emergency(
    gear: DaliGearData, expected: bool
) -> None:
    """Test only dimmable, colour, or maintained emergency gear become lights."""
    assert _is_light(gear) is expected


async def test_light_setup_adds_new_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test the light platform creates entities and registers a listener."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.components.dali.DriverLubaRs232",
        return_value=fake_dali_driver,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    entity_entries = [
        entity
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if entity.domain == LIGHT_DOMAIN
    ]
    assert len(entity_entries) == 2
    assert {
        entity.unique_id.rsplit("_", maxsplit=1)[-1] for entity in entity_entries
    } == {"0", "2"}


async def test_dt8_light_properties_and_color_commands(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test DT8 properties and color-temperature/XY commands."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        2: DaliGearData(
            level=255,
            device_types=(8,),
            supports_color_temperature=True,
            supports_xy=True,
            color_type_tc_active=False,
            color_temp_mired=250,
            color_temp_min_mired=153,
            color_temp_max_mired=500,
            xy_color=(0.5, 0.25),
        )
    }
    light = DaliLight(coordinator, entry.entry_id, entry.title, 2)

    assert light.color_mode is ColorMode.XY
    assert light.color_temp_kelvin == 4000
    assert light.xy_color == pytest.approx((0.5, 0.25), abs=1e-5)
    assert light._attr_min_color_temp_kelvin == 2000
    assert light._attr_max_color_temp_kelvin == 6535

    with (
        patch.object(coordinator, "async_update_gear", new_callable=AsyncMock),
        patch.object(fake_dali_driver, "send", new_callable=AsyncMock) as send,
    ):
        await light.async_turn_on(color_temp_kelvin=4000, xy_color=(0.1, 0.2))

    assert send.await_count == 2


async def test_color_temperature_change_preserves_brightness(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test setting only color temperature does not drive the arc level."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        2: DaliGearData(
            level=128,
            device_types=(8,),
            supports_color_temperature=True,
            color_temp_mired=250,
        )
    }
    light = DaliLight(coordinator, entry.entry_id, entry.title, 2)

    with (
        patch.object(coordinator, "async_update_gear", new_callable=AsyncMock),
        patch.object(fake_dali_driver, "run_sequence", new_callable=AsyncMock),
    ):
        await light.async_turn_on(color_temp_kelvin=4000)

    assert not any(isinstance(command, DAPC) for command in fake_dali_driver.sent)


async def test_color_temperature_only_mode(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test DT8 color temperature mode when XY is unavailable."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        2: DaliGearData(
            level=128,
            device_types=(8,),
            supports_color_temperature=True,
            color_temp_mired=250,
        )
    }
    light = DaliLight(coordinator, entry.entry_id, entry.title, 2)

    assert light.color_mode is ColorMode.COLOR_TEMP


async def test_color_temperature_mode_without_readback(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a color-temperature gear reports COLOR_TEMP even without a value."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        2: DaliGearData(
            level=128,
            device_types=(8,),
            supports_color_temperature=True,
            color_temp_mired=None,
        )
    }
    light = DaliLight(coordinator, entry.entry_id, entry.title, 2)

    assert light.color_mode is ColorMode.COLOR_TEMP
    assert light.color_temp_kelvin is None


async def test_dual_mode_reports_active_color_temperature(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a dual-mode gear reports COLOR_TEMP when Tc is the active type."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        2: DaliGearData(
            level=255,
            device_types=(8,),
            supports_color_temperature=True,
            supports_xy=True,
            color_type_tc_active=True,
            color_temp_mired=250,
            xy_color=(0.5, 0.25),
        )
    }
    light = DaliLight(coordinator, entry.entry_id, entry.title, 2)

    assert light.color_mode is ColorMode.COLOR_TEMP


async def test_color_modes_are_not_shared_between_lights(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a DT8 light does not change the color modes of a DT6 light."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {
        0: DaliGearData(level=128, device_types=(6,)),
        2: DaliGearData(level=128, device_types=(8,), supports_xy=True),
    }
    dt6_light = DaliLight(coordinator, entry.entry_id, entry.title, 0)
    DaliLight(coordinator, entry.entry_id, entry.title, 2)

    assert dt6_light.supported_color_modes == {ColorMode.BRIGHTNESS}


@pytest.mark.parametrize("min_level", [1, 86, 128])
@pytest.mark.parametrize("linear_dimming", [False, True])
def test_brightness_level_round_trip_is_stable(
    min_level: int, linear_dimming: bool
) -> None:
    """Test set-then-read of a brightness settles to a fixed point.

    Reading the sent level back must reproduce a brightness that maps to the
    same level again, so the UI slider does not keep drifting after a command.
    """
    for brightness in range(1, 256):
        level = _brightness_to_dali_level(brightness, linear_dimming, min_level)
        assert min_level <= level <= 254
        read_back = _dali_level_to_brightness(level, linear_dimming, min_level)
        assert _brightness_to_dali_level(read_back, linear_dimming, min_level) == level


@pytest.mark.parametrize("min_level", [1, 86, 128])
@pytest.mark.parametrize("linear_dimming", [False, True])
def test_brightness_to_level_snaps_to_closest_readback(
    min_level: int, linear_dimming: bool
) -> None:
    """Test the chosen level reads back closer than any nearby level."""
    for brightness in range(1, 256):
        level = _brightness_to_dali_level(brightness, linear_dimming, min_level)
        chosen_error = abs(
            _dali_level_to_brightness(level, linear_dimming, min_level) - brightness
        )
        for other in range(max(min_level, level - 5), min(254, level + 5) + 1):
            other_error = abs(
                _dali_level_to_brightness(other, linear_dimming, min_level) - brightness
            )
            assert chosen_error <= other_error


def test_set_xy_color_sequence() -> None:
    """Test DT8 xy values are converted to DALI DTR commands."""
    commands = list(_set_xy_color(GearShort(2), (0.25, 0.5)))

    assert len(commands) == 7


async def test_light_turn_on_failure_raises_home_assistant_error(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test turn-on communication errors are surfaced."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {0: DaliGearData(level=128, device_types=(6,))}
    light = DaliLight(coordinator, entry.entry_id, entry.title, 0)

    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("offline")),
        pytest.raises(HomeAssistantError),
    ):
        await light.async_turn_on()
