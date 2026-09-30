"""Test the Tridonic DALI coordinator."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dali.address import DeviceShort, GearShort
from dali.exceptions import DALISequenceError
from dali.gear.colour import QueryColourValueDTR
from dali.gear.general import QueryControlGearPresent
from dali.sequences import progress
import pytest

from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    _dali_level_to_brightness,
    _log_commissioning_progress,
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
        side_effect=[[6], None, DALISequenceError()],
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


async def test_dt8_color_capabilities(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test DT8 color-space capabilities and values are queried."""
    gear = await coordinator._query_gear(2)

    assert gear is not None
    assert gear.supports_color_temperature
    assert gear.supports_xy
    assert gear.color_temp_mired == 250
    assert gear.color_temp_min_mired == 153
    assert gear.color_temp_max_mired == 500
    assert gear.xy_color == pytest.approx((0.5, 0.25), abs=1e-5)


async def test_configured_tc_limits_take_precedence(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the configured Tc limits are preferred over the physical ones."""
    fake_dali_driver.color_values[QueryColourValueDTR.ColourTemperatureTcCoolest] = 200
    fake_dali_driver.color_values[QueryColourValueDTR.ColourTemperatureTcWarmest] = 370

    gear = await coordinator._query_gear(2)

    assert gear is not None
    assert gear.color_temp_min_mired == 200
    assert gear.color_temp_max_mired == 370


async def test_unanswered_capability_queries_use_defaults(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a gear that ignores capability queries falls back to defaults."""
    original_run_sequence = fake_dali_driver.run_sequence

    async def run_sequence(sequence: Generator, progress: object = None) -> object:
        if sequence.__name__ == "QueryDT6DimmingCurve":
            sequence.close()
            return None
        return await original_run_sequence(sequence, progress)

    fake_dali_driver.color_features = {2: None}
    with patch.object(fake_dali_driver, "run_sequence", side_effect=run_sequence):
        gear_dt6 = await coordinator._query_gear(0)
        gear_dt8 = await coordinator._query_gear(2)

    assert gear_dt6 is not None
    assert gear_dt6.linear_dimming is False
    assert gear_dt8 is not None
    assert gear_dt8.supports_color_temperature is False
    assert gear_dt8.supports_xy is False


async def test_async_update_gear_touches_single_entry(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a targeted update refreshes one gear without a full bus scan."""
    coordinator.data = {5: SimpleNamespace()}

    await coordinator.async_update_gear(0)

    assert 0 in coordinator.data
    assert not any(
        isinstance(command, QueryControlGearPresent)
        and command.destination.address != 0
        for command in fake_dali_driver.sent
    )


async def test_async_update_gear_drops_absent_gear(
    coordinator: DaliCoordinator,
) -> None:
    """Test a targeted update removes a gear that stopped responding."""
    coordinator.data = {5: SimpleNamespace()}

    await coordinator.async_update_gear(5)

    assert 5 not in coordinator.data


async def test_known_gear_survives_transient_presence_miss(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a known gear is re-queried after a dropped presence response."""
    coordinator.data = {0: SimpleNamespace()}
    original_send = fake_dali_driver.send
    attempts = 0

    async def send(command: object) -> object:
        nonlocal attempts
        if (
            isinstance(command, QueryControlGearPresent)
            and command.destination.address == 0
        ):
            attempts += 1
            if attempts == 1:
                return SimpleNamespace(value=False)
        return await original_send(command)

    with patch.object(fake_dali_driver, "send", side_effect=send):
        gear = await coordinator._query_gear(0)

    assert attempts == 2
    assert gear is not None


async def test_absent_address_is_not_retried(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test an unknown address is not re-queried, avoiding extra bus traffic."""
    attempts = 0
    original_send = fake_dali_driver.send

    async def send(command: object) -> object:
        nonlocal attempts
        if (
            isinstance(command, QueryControlGearPresent)
            and command.destination.address == 5
        ):
            attempts += 1
        return await original_send(command)

    with patch.object(fake_dali_driver, "send", side_effect=send):
        gear = await coordinator._query_gear(5)

    assert attempts == 1
    assert gear is None


async def test_device_instance_metadata_and_event_listener(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test device metadata caching and event dispatch."""
    fake_dali_driver.dev_inst_map.mapping = {(1, 2): 3}
    coordinator = DaliCoordinator(
        hass, fake_dali_driver, MockConfigEntry(domain=DOMAIN)
    )
    await coordinator._async_update_data()
    received: list[object] = []
    remove_listener = coordinator.async_add_event_listener(received.append)
    coordinator.start_event_listener()

    event = SimpleNamespace(short_address=DeviceShort(1), instance_number=2)
    await fake_dali_driver.event_queue.put(object())
    await fake_dali_driver.event_queue.put(event)
    await hass.async_block_till_done()
    remove_listener()
    await fake_dali_driver.event_queue.put(event)
    await hass.async_block_till_done()

    try:
        assert received == [event]
        assert coordinator._device_metadata[1].gtin == 1234567654321
    finally:
        coordinator.close()
        await hass.async_block_till_done()


async def test_device_info_for_unknown_address(
    coordinator: DaliCoordinator,
) -> None:
    """Test device info falls back to defaults without known metadata."""
    coordinator.data = {}

    device_info = coordinator.device_info("entry", "USB-DALI2", 5)

    assert device_info["model"] == "DALI control gear"
    assert device_info["serial_number"] is None


async def test_part_306_instance_is_discovered(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test Part 306 instances are queried for measurement metadata."""
    fake_dali_driver.dev_inst_map.mapping = {(1, 2): 6}
    coordinator = DaliCoordinator(
        hass, fake_dali_driver, MockConfigEntry(domain=DOMAIN)
    )

    await coordinator._async_update_data()

    sensor = coordinator.input_devices[(1, 2)]
    assert sensor.quantity_name == 27
    assert sensor.unit_of_measurement == 9
    assert sensor.resolution == 9
    assert sensor.magnitude == 127
    assert sensor.input_signal_signed is False
    assert sensor.gtin == 1234567654321


async def test_part_306_query_failure_raises_update_failed(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test Part 306 query failures become coordinator update failures."""
    coordinator = DaliCoordinator(
        hass, fake_dali_driver, MockConfigEntry(domain=DOMAIN)
    )

    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("offline")),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._query_general_sensor(DeviceShort(1), 2)


async def test_missing_short_address_query_failure(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a failed missing-short-address query becomes an update failure."""
    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("offline")),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._has_unaddressed_gear()


@pytest.mark.parametrize(
    ("update", "logged"),
    [
        pytest.param("Addressing complete", True, id="string"),
        pytest.param(progress(message="Ballast found"), True, id="message"),
        pytest.param(progress(completed=1, size=2), False, id="no_message"),
    ],
)
def test_commissioning_progress_logging(
    caplog: pytest.LogCaptureFixture, update: str | progress, logged: bool
) -> None:
    """Test commissioning progress messages are logged."""
    with caplog.at_level("DEBUG"):
        _log_commissioning_progress(update)

    assert ("Commissioning:" in caplog.text) is logged
