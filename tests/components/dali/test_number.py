"""Test the Tridonic DALI number platform (gear configuration variables)."""

from unittest.mock import patch

from dali.address import GearShort
from dali.gear.general import DTR0, SetMinLevel
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import DaliCoordinator
from homeassistant.components.dali.helpers import (
    encode_extended_fade_time,
    extended_fade_time_seconds,
    set_dtr0_variable,
)
from homeassistant.components.dali.number import NUMBER_TYPES, DaliGearNumber
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeDaliDriver

MIN_LEVEL_ENTITY = "number.usb_dali2_0_gear_minimum_level"
MAX_LEVEL_ENTITY = "number.usb_dali2_0_gear_maximum_level"
EXTENDED_FADE_TIME_ENTITY = "number.usb_dali2_0_gear_extended_fade_time"
SYSTEM_FAILURE_LEVEL_ENTITY = "number.usb_dali2_0_gear_system_failure_level"


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


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, 0.0),
        (0.1, 0.1),
        (1.6, 1.6),
        (1.7, 2.0),
        (16, 16.0),
        (45, 50.0),
        (60, 60.0),
        (160, 160.0),
        (960, 960.0),
        (1200, 960.0),
    ],
    ids=[
        "zero",
        "finest_step",
        "finest_maximum",
        "next_multiplier",
        "second_multiplier_maximum",
        # Python's banker's rounding would snap this down to 40 seconds.
        "rounds_half_up",
        "minute_unit",
        "ten_second_maximum",
        "maximum",
        "above_maximum_clamps",
    ],
)
def test_extended_fade_time_round_trip(seconds: float, expected: float) -> None:
    """Test a requested extended fade time snaps to the nearest usable step."""
    byte = encode_extended_fade_time(seconds)
    assert extended_fade_time_seconds(byte & 0x0F, (byte >> 4) & 0x07) == expected


@pytest.mark.parametrize(
    ("base", "multiplier"),
    [(None, 1), (5, None), (3, 5), (3, 7)],
    ids=["no_base", "no_multiplier", "reserved_multiplier", "highest_reserved"],
)
def test_extended_fade_time_not_representable(
    base: int | None, multiplier: int | None
) -> None:
    """Test unreadable and reserved encodings have no value in seconds."""
    assert extended_fade_time_seconds(base, multiplier) is None


async def test_numbers_report_the_stored_configuration(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the numbers report what the gear has stored."""
    await _setup(hass, fake_dali_driver)

    assert hass.states.get(MIN_LEVEL_ENTITY).state == "86"
    assert hass.states.get(MAX_LEVEL_ENTITY).state == "254"
    assert hass.states.get(SYSTEM_FAILURE_LEVEL_ENTITY).state == "200"
    assert hass.states.get(EXTENDED_FADE_TIME_ENTITY).state == "60.0"


async def test_mask_system_failure_level_reads_as_a_level(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test gear holding its level on a bus failure reads 255, not unknown.

    MASK is the power-up default, so it has to be distinguishable from a gear
    that did not answer the query at all.
    """
    await _setup(hass, fake_dali_driver)

    assert (
        hass.states.get("number.usb_dali2_2_gear_system_failure_level").state == "255"
    )


async def test_extended_fade_time_absent_when_unsupported(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test gear that does not answer the query gets no extended fade time."""
    await _setup(hass, fake_dali_driver)

    # Only gear 0 implements it in the fake.
    assert hass.states.get("number.usb_dali2_2_gear_extended_fade_time") is None
    assert hass.states.get("number.usb_dali2_2_gear_minimum_level") is not None


@pytest.mark.parametrize(
    ("entity_id", "value", "expected_write", "expected_state"),
    [
        (MIN_LEVEL_ENTITY, 1, (0, "SetMinLevel", 1), "86"),
        (MIN_LEVEL_ENTITY, 100, (0, "SetMinLevel", 100), "100"),
        (MAX_LEVEL_ENTITY, 200, (0, "SetMaxLevel", 200), "200"),
        (
            SYSTEM_FAILURE_LEVEL_ENTITY,
            0,
            (0, "SetSystemFailureLevel", 0),
            "0",
        ),
        (
            SYSTEM_FAILURE_LEVEL_ENTITY,
            255,
            (0, "SetSystemFailureLevel", 255),
            "255",
        ),
        (
            EXTENDED_FADE_TIME_ENTITY,
            45,
            (0, "SetExtendedFadeTime", 0x34),
            "50.0",
        ),
    ],
    ids=[
        "min_level_clamped_to_physical_minimum",
        "min_level_accepted",
        "max_level_accepted",
        "system_failure_level_off",
        "system_failure_level_mask",
        "extended_fade_time_snapped",
    ],
)
async def test_setting_a_number_writes_and_reads_back(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    entity_id: str,
    value: float,
    expected_write: tuple[int, str, int],
    expected_state: str,
) -> None:
    """Test a write reaches the gear and the stored value is read back."""
    await _setup(hass, fake_dali_driver)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value},
        blocking=True,
    )

    assert fake_dali_driver.gear_writes[-1] == expected_write
    assert hass.states.get(entity_id).state == expected_state


async def test_number_write_failure_raises_home_assistant_error(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test DALI communication errors are surfaced as Home Assistant errors."""
    await _setup(hass, fake_dali_driver)

    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("serial failure")),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: MIN_LEVEL_ENTITY, ATTR_VALUE: 10},
            blocking=True,
        )


def test_set_dtr0_variable_loads_the_value_before_the_command() -> None:
    """Test the write sequence stages the value in DTR0 first.

    The SET commands carry only the destination address, so the two frames have
    to be issued as one transaction in this order.
    """
    commands = list(set_dtr0_variable(SetMinLevel, GearShort(3), 42))

    assert isinstance(commands[0], DTR0)
    assert commands[0].param == 42
    assert isinstance(commands[1], SetMinLevel)
    assert commands[1].destination.address == 3


def test_number_has_no_value_without_gear_data(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a number reports no value while its gear is off the bus."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"})
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {}
    description = next(d for d in NUMBER_TYPES if d.key == "min_level")
    number = DaliGearNumber(coordinator, "entry", "USB-DALI2", 0, description)

    assert not number.available
    assert number.native_value is None
