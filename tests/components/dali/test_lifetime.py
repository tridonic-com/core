"""Test the Tridonic Lifetime Indicator read out of memory bank 4."""

from decimal import Decimal
from unittest.mock import patch

from dali.memory import info
from dali.memory.location import FlagValue, MemoryValue
import pytest

from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.lifetime import (
    OPERATING_TIME_FIRST_ADDRESS,
    OPERATING_TIME_VALUES,
    TEMPERATURE_RANGES,
    AgingRateLastMonth,
    AgingRateLastYear,
    CurrentAgingRate,
    DriverHealth,
)
from homeassistant.const import CONF_DEVICE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry

# A GTIN carrying Tridonic's GS1 company prefix, which is what gates the
# manufacturer-specific bank 4 read.
TRIDONIC_GTIN = 9006210123456

# The Lifetime Indicator block ends the bank.
BANK_4_LAST_ADDRESS = 0x67

OPERATING_TIME_RANGE_1 = OPERATING_TIME_VALUES[0]
OPERATING_TIME_RANGE_17 = OPERATING_TIME_VALUES[-1]


def _bank_image(contents: dict[int, int]) -> list[int | None]:
    """Return a bank 4 memory image holding the given locations.

    Locations the caller leaves out read as unimplemented, which is how a
    driver answers for a value it does not report.
    """
    image: list[int | None] = [None] * (BANK_4_LAST_ADDRESS + 1)
    image[0x00] = BANK_4_LAST_ADDRESS
    for address, value in contents.items():
        image[address] = value
    return image


@pytest.mark.parametrize(
    ("value_class", "contents", "expected"),
    [
        pytest.param(DriverHealth, {0x42: 0xC8}, Decimal("100.0"), id="health_new"),
        pytest.param(DriverHealth, {0x42: 0x4B}, Decimal("37.5"), id="health_worn"),
        pytest.param(DriverHealth, {0x42: 0x00}, Decimal("0.0"), id="health_spent"),
        pytest.param(
            DriverHealth, {0x42: 0xFF}, FlagValue.Invalid, id="health_above_100_percent"
        ),
        pytest.param(
            CurrentAgingRate, {0x43: 0x7D}, Decimal("25.0"), id="current_aging_rate"
        ),
        pytest.param(
            CurrentAgingRate,
            {0x43: 0xFB},
            FlagValue.Invalid,
            id="current_aging_rate_over_range",
        ),
        pytest.param(
            AgingRateLastMonth, {0x44: 0xFA}, Decimal("25.0"), id="aging_last_month"
        ),
        pytest.param(
            AgingRateLastYear, {0x45: 0x0A}, Decimal("1.0"), id="aging_last_year"
        ),
        pytest.param(
            AgingRateLastYear,
            {0x45: 0xFB},
            FlagValue.Invalid,
            id="aging_last_year_over_range",
        ),
        # The two counter bytes are stored least significant byte first.
        pytest.param(
            OPERATING_TIME_RANGE_1,
            {0x46: 0x34, 0x47: 0x12},
            9320,
            id="operating_time_little_endian",
        ),
        pytest.param(
            OPERATING_TIME_RANGE_1,
            {0x46: 0xFF, 0x47: 0xFF},
            131070,
            id="operating_time_full_counter",
        ),
        pytest.param(
            OPERATING_TIME_RANGE_17,
            {0x66: 0x02, 0x67: 0x01},
            516,
            id="operating_time_last_range",
        ),
    ],
)
def test_bank_4_values_decode_as_specified(
    value_class: type[MemoryValue],
    contents: dict[int, int],
    expected: Decimal | int | FlagValue,
) -> None:
    """Test the Lifetime Indicator block decodes to its specified values."""
    assert value_class.from_list(_bank_image(contents)) == expected


def test_operating_time_counters_cover_the_whole_block() -> None:
    """Test one counter per temperature range fills 0x46 to the bank end."""
    addresses = [
        location.address
        for value_class in OPERATING_TIME_VALUES
        for location in value_class.locations
    ]

    assert len(OPERATING_TIME_VALUES) == len(TEMPERATURE_RANGES)
    assert sorted(addresses) == list(
        range(OPERATING_TIME_FIRST_ADDRESS, BANK_4_LAST_ADDRESS + 1)
    )


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


# Bank 4 as the gear at address 0 of the fake driver answers it.
LIFETIME_BANK_4: dict[int, dict[object, object]] = {
    0: {
        DriverHealth: Decimal("99.0"),
        CurrentAgingRate: Decimal("5.2"),
        AgingRateLastMonth: Decimal("4.8"),
        AgingRateLastYear: Decimal("3.9"),
        OPERATING_TIME_RANGE_1: 9320,
        OPERATING_TIME_RANGE_17: 516,
    }
}


@pytest.fixture
def tridonic_dali_driver(fake_dali_driver: FakeDaliDriver) -> FakeDaliDriver:
    """Return a fake driver whose control gear is a Tridonic LED driver."""
    fake_dali_driver.metadata[info.GTIN] = TRIDONIC_GTIN
    fake_dali_driver.bank_memory[4] = LIFETIME_BANK_4
    return fake_dali_driver


def _entity_id(
    entity_registry: er.EntityRegistry, entry: MockConfigEntry, key: str
) -> str | None:
    """Return the entity ID of a Lifetime Indicator sensor of gear 0."""
    return entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_0_{key}"
    )


@pytest.mark.parametrize(
    ("key", "expected_state", "expected_unit"),
    [
        ("driver_health", "99.0", "%"),
        ("current_aging_rate", "5.2", "%/year"),
        ("aging_rate_last_month", "4.8", "%/year"),
        ("aging_rate_last_year", "3.9", "%/year"),
    ],
)
async def test_lifetime_sensors_report_the_bank_4_values(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    tridonic_dali_driver: FakeDaliDriver,
    key: str,
    expected_state: str,
    expected_unit: str,
) -> None:
    """Test the Lifetime Indicator values of a Tridonic driver reach the states."""
    entry = await _setup(hass, tridonic_dali_driver)

    entity_id = _entity_id(entity_registry, entry, key)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected_state
    assert state.attributes["unit_of_measurement"] == expected_unit
    assert (
        entity_registry.async_get(entity_id).entity_category
        == EntityCategory.DIAGNOSTIC
    )


async def test_operating_time_counters_are_disabled_until_enabled(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    tridonic_dali_driver: FakeDaliDriver,
) -> None:
    """Test the per-temperature counters only appear once asked for.

    Seventeen counters per driver would swamp the dashboard, so they are
    registered disabled and start reporting after being enabled.
    """
    entry = await _setup(hass, tridonic_dali_driver)

    entity_id = _entity_id(entity_registry, entry, "operating_time_temperature_range_1")
    assert entity_id is not None
    assert (
        entity_registry.async_get(entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    assert hass.states.get(entity_id) is None

    entity_registry.async_update_entity(entity_id, disabled_by=None)
    with patch(
        "homeassistant.components.dali.DriverLubaRs232",
        return_value=tridonic_dali_driver,
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "9320"
    assert state.attributes["unit_of_measurement"] == "h"


@pytest.mark.parametrize(
    ("gtin", "bank_4"),
    [
        pytest.param(1234567654321, LIFETIME_BANK_4, id="gear_of_another_manufacturer"),
        pytest.param(TRIDONIC_GTIN, {}, id="tridonic_gear_without_the_bank"),
    ],
)
async def test_no_lifetime_sensors_without_a_tridonic_bank_4(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    fake_dali_driver: FakeDaliDriver,
    gtin: int,
    bank_4: dict[int, dict[object, object]],
) -> None:
    """Test bank 4 is only read out of gear that answers a Tridonic GTIN.

    Bank 4 is manufacturer-specific, so another manufacturer is free to store
    something entirely different there; reading it as a Lifetime Indicator
    would report invented health figures.
    """
    fake_dali_driver.metadata[info.GTIN] = gtin
    fake_dali_driver.bank_memory[4] = bank_4

    entry = await _setup(hass, fake_dali_driver)

    assert _entity_id(entity_registry, entry, "driver_health") is None
    assert _entity_id(entity_registry, entry, "current_aging_rate") is None
    assert (
        _entity_id(entity_registry, entry, "operating_time_temperature_range_1") is None
    )
