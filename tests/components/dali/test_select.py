"""Test the Tridonic DALI select platform (fade time and fade rate)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from homeassistant.components import dali
from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import DaliCoordinator
from homeassistant.components.dali.helpers import FADE_RATE_OPTIONS, FADE_TIME_OPTIONS
from homeassistant.components.dali.select import SELECT_TYPES, DaliGearSelect
from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_DEVICE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry

FADE_TIME_ENTITY = "select.usb_dali2_0_gear_fade_time"
FADE_RATE_ENTITY = "select.usb_dali2_0_gear_fade_rate"


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


async def test_selects_report_the_stored_fade_settings(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the selects decode the fade time and rate nibbles."""
    await _setup(hass, fake_dali_driver)

    fade_time = hass.states.get(FADE_TIME_ENTITY)
    assert fade_time.state == "2_0_s"
    assert len(fade_time.attributes[ATTR_OPTIONS]) == 16

    fade_rate = hass.states.get(FADE_RATE_ENTITY)
    assert fade_rate.state == "44_7_steps_s"
    assert len(fade_rate.attributes[ATTR_OPTIONS]) == 15


@pytest.mark.parametrize(
    ("entity_id", "option", "expected_write"),
    [
        (FADE_TIME_ENTITY, "8_0_s", (0, "SetFadeTime", 8)),
        (FADE_TIME_ENTITY, "extended", (0, "SetFadeTime", 0)),
        (FADE_RATE_ENTITY, "2_8_steps_s", (0, "SetFadeRate", 15)),
    ],
    ids=["fade_time", "fade_time_extended", "fade_rate"],
)
async def test_selecting_an_option_writes_and_reads_back(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    entity_id: str,
    option: str,
    expected_write: tuple[int, str, int],
) -> None:
    """Test a selected option reaches the gear and is read back from it."""
    await _setup(hass, fake_dali_driver)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: option},
        blocking=True,
    )

    assert fake_dali_driver.gear_writes[-1] == expected_write
    assert hass.states.get(entity_id).state == option


@pytest.mark.parametrize(
    ("fade", "expected_time", "expected_rate"),
    [
        ((4, 0), "2_0_s", STATE_UNKNOWN),
        (None, STATE_UNKNOWN, STATE_UNKNOWN),
    ],
    ids=["reserved_fade_rate_code", "framing_error"],
)
async def test_unreportable_code_reads_unknown_but_stays_available(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    fade: tuple[int, int] | None,
    expected_time: str,
    expected_rate: str,
) -> None:
    """Test a code the gear cannot report reads unknown rather than erroring."""
    fake_dali_driver.fade[0] = fade
    await _setup(hass, fake_dali_driver)

    assert hass.states.get(FADE_TIME_ENTITY).state == expected_time
    assert hass.states.get(FADE_RATE_ENTITY).state == expected_rate


async def test_select_write_failure_raises_home_assistant_error(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test DALI communication errors are surfaced as Home Assistant errors."""
    await _setup(hass, fake_dali_driver)

    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("serial failure")),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: FADE_TIME_ENTITY, ATTR_OPTION: "8_0_s"},
            blocking=True,
        )


@pytest.mark.parametrize(
    ("key", "options"),
    [("fade_time", FADE_TIME_OPTIONS), ("fade_rate", FADE_RATE_OPTIONS)],
)
def test_every_option_is_translated(key: str, options: dict[int, str]) -> None:
    """Test each option slug has a translation.

    hassfest validates the translation file's shape but cannot see the option
    lists, so an added or renamed code would otherwise go unnoticed until the
    entity rendered a raw slug in the UI.
    """
    translations = json.loads(
        (Path(dali.__file__).parent / "translations" / "en.json").read_text(
            encoding="utf-8"
        )
    )
    states = translations["entity"]["select"][key]["state"]

    assert set(options.values()) == set(states)


def test_select_has_no_option_without_gear_data(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a select reports no option while its gear is off the bus."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {}
    select = DaliGearSelect(coordinator, "entry", "USB-DALI2", 0, SELECT_TYPES[0])

    assert not select.available
    assert select.current_option is None
