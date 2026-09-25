"""Test the Tridonic DALI coordinator."""

from unittest.mock import AsyncMock, patch

from dali.address import GearShort
from dali.exceptions import DALISequenceError
import pytest

from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    _dali_level_to_brightness,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry


@pytest.fixture
def coordinator(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> DaliCoordinator:
    """Return a DALI coordinator with a fake driver."""
    entry = MockConfigEntry(domain=DOMAIN)
    return DaliCoordinator(hass, fake_dali_driver, entry)


@pytest.mark.parametrize(
    ("level", "linear", "expected"),
    [(255, False, None), (0, False, 0), (86, False, 1), (170, True, 128)],
)
def test_dali_level_to_brightness(
    level: object, linear: bool, expected: int | None
) -> None:
    """Test DALI readback conversion branches."""
    assert _dali_level_to_brightness(level, linear, 86) == expected


async def test_invalid_min_level_uses_default(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test invalid physical minimum levels fall back to one."""
    fake_dali_driver.min_levels[0] = 255

    gear = await coordinator._query_gear(0)

    assert gear is not None
    assert gear.min_level == 1


async def test_metadata_failure_is_ignored(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test optional metadata failures do not discard a responding gear."""
    with patch.object(
        fake_dali_driver,
        "run_sequence",
        new_callable=AsyncMock,
        side_effect=[[6], DALISequenceError()],
    ):
        gear = await coordinator._query_gear(0)

    assert gear is not None
    assert gear.gtin is None


async def test_communication_failure_raises_update_failed(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test communication failures become coordinator update failures."""
    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("offline")),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._query_gear(0)


async def test_metadata_success(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test bank metadata is copied into gear data."""
    gear = await coordinator._query_metadata(GearShort(0))

    assert gear.gtin == 1234567654321
    assert gear.identification_number == 1
    assert gear.firmware_version == "1.0"
    assert gear.hardware_version == "2.1"
