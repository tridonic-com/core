"""Test the Tridonic DALI coordinator."""

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dali.address import DeviceShort, GearShort
from dali.device.general import (
    DTR0 as DeviceDTR0,
    EnableInstance,
    EventScheme,
    QueryResolution,
    SetEventFilter,
    SetEventScheme,
)
from dali.exceptions import DALISequenceError
from dali.gear.colour import QueryColourValueDTR
from dali.gear.general import QueryControlGearPresent
from dali.sequences import progress
import pytest

from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    DaliGearData,
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


async def test_initial_refresh_defers_slow_memory_reads(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the first refresh skips identification and emergency diagnostics."""
    data = await coordinator._async_update_data()

    # Identification metadata (memory bank 0) is not read during the fast pass.
    assert data[0].gtin is None
    assert coordinator._metadata == {}
    # Emergency status is read, but the slow bank-208 diagnostics are deferred.
    assert data[1].emergency is not None
    assert data[1].emergency.status == 0b00001000
    assert data[1].emergency.control_gear_temperature is None

    # A later full refresh reads the deferred data.
    coordinator._last_full_scan = None
    data = await coordinator._async_update_data()

    assert data[0].gtin == 1234567654321
    assert data[1].emergency is not None
    assert data[1].emergency.control_gear_temperature == 25


async def test_metadata_cache_is_restored(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    hass_storage: dict[str, Any],
) -> None:
    """Test persisted identification data is reused instead of re-read."""
    entry = MockConfigEntry(domain=DOMAIN)
    hass_storage[f"{DOMAIN}.{entry.entry_id}_metadata"] = {
        "version": 1,
        "data": {
            "gear": {
                "0": {
                    "gtin": 42,
                    "identification_number": 7,
                    "firmware_version": "9.9",
                    "hardware_version": "8.8",
                }
            },
            "device": {"3": {"identification_number": 11}},
            "sensor": {"3:0": {"quantity_name": 1, "resolution": 9}},
        },
    }
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)

    await coordinator.async_load_metadata_cache()

    assert coordinator._metadata[0].gtin == 42
    assert coordinator._device_metadata[3].identification_number == 11
    assert coordinator.input_devices[(3, 0)].resolution == 9
    # The fast initial pass serves the cached value without a bank-0 read, so
    # the gear keeps the restored GTIN rather than the one the bus would report.
    gear = await coordinator._query_gear(0, fast=True)
    assert gear is not None
    assert gear.gtin == 42


async def test_metadata_is_persisted_after_read(
    coordinator: DaliCoordinator,
) -> None:
    """Test freshly read identification data is written to the store."""
    await coordinator._query_gear(0)

    snapshot = coordinator._metadata_snapshot()

    assert snapshot["gear"]["0"]["gtin"] == 1234567654321
    assert snapshot["gear"]["0"]["firmware_version"] == "1.0"


async def test_enrich_deferred_data_survives_transport_error(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a transport error during deferred enrichment is swallowed."""
    await coordinator.async_refresh()

    with patch.object(fake_dali_driver, "run_sequence", side_effect=OSError("offline")):
        await coordinator.async_enrich_deferred_data()

    # Enrichment never completed, so no identification metadata was cached; the
    # periodic refresh is left to retry rather than the error propagating.
    assert coordinator._metadata == {}


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


async def test_emergency_information_is_decoded(
    coordinator: DaliCoordinator,
) -> None:
    """Test DT1 gear is queried for emergency status, measurements and MB208."""
    gear = await coordinator._query_gear(1)

    assert gear is not None
    assert gear.emergency is not None
    emergency = gear.emergency
    assert emergency.mode == 0b00000010
    assert emergency.features == 0b00000001
    assert emergency.status == 0b00001000
    assert emergency.failure_status == 0x00
    assert emergency.battery_charge == 254
    assert emergency.emergency_level == 254
    assert emergency.duration_test_result == 60
    assert emergency.rated_duration == 90
    assert emergency.lamp_emergency_time == 5
    assert emergency.lamp_total_operation_time == 100
    assert emergency.control_gear_temperature == 25
    assert emergency.average_power_during_charging == 5.0
    assert emergency.start_function_test_counter == 12
    assert emergency.controllable is False


async def test_emergency_memory_failure_leaves_diagnostics_unset(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test an MB208 read failure keeps the status data but drops diagnostics."""
    original_run_sequence = fake_dali_driver.run_sequence

    async def run_sequence(sequence: Generator, progress: object = None) -> object:
        if (
            sequence.__name__ == "read_all"
            and sequence.gi_frame.f_locals.get("self").address == 208
        ):
            sequence.close()
            raise DALISequenceError
        return await original_run_sequence(sequence, progress)

    with patch.object(fake_dali_driver, "run_sequence", side_effect=run_sequence):
        gear = await coordinator._query_gear(1)

    assert gear is not None
    assert gear.emergency is not None
    assert gear.emergency.battery_charge == 254
    assert gear.emergency.control_gear_temperature is None
    assert gear.emergency.start_function_test_counter is None


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
    # Identification data is read lazily after the fast initial refresh.
    await coordinator.async_enrich_deferred_data()
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


async def test_general_sensor_metadata_is_decoded(
    coordinator: DaliCoordinator,
) -> None:
    """Test stable metadata reads populate the sensor data."""
    sensor = await coordinator._query_general_sensor(DeviceShort(1), 0)

    assert sensor.resolution == 9
    assert sensor.magnitude == 127
    assert sensor.input_signal_signed is False


async def test_general_sensor_rejects_single_corrupt_resolution(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a one-off out-of-range resolution is discarded, not latched."""
    original_send = fake_dali_driver.send
    responses = iter([512, 9, 9])

    async def send(command: object) -> object:
        if isinstance(command, QueryResolution):
            return SimpleNamespace(value=next(responses))
        return await original_send(command)

    with patch.object(fake_dali_driver, "send", side_effect=send):
        sensor = await coordinator._query_general_sensor(DeviceShort(1), 0)

    assert sensor.resolution == 9


async def test_general_sensor_unstable_resolution_raises(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test metadata that never agrees across reads fails the update."""
    original_send = fake_dali_driver.send
    responses = iter([9, 11, 13])

    async def send(command: object) -> object:
        if isinstance(command, QueryResolution):
            return SimpleNamespace(value=next(responses))
        return await original_send(command)

    with (
        patch.object(fake_dali_driver, "send", side_effect=send),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._query_general_sensor(DeviceShort(1), 0)


async def test_general_sensor_rejects_out_of_range_resolution(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a persistently out-of-range resolution fails the update."""
    fake_dali_driver.resolution = 99

    with pytest.raises(UpdateFailed):
        await coordinator._query_general_sensor(DeviceShort(1), 0)


@pytest.mark.parametrize(
    ("input_device", "expected_name", "expected_model"),
    [
        pytest.param(False, "USB-DALI2/5 (gear)", "DALI control gear", id="gear"),
        pytest.param(True, "USB-DALI2/5 (sensor)", "DALI control device", id="sensor"),
    ],
)
async def test_device_info_for_unknown_address(
    coordinator: DaliCoordinator,
    input_device: bool,
    expected_name: str,
    expected_model: str,
) -> None:
    """Test device info falls back to defaults and labels the device kind."""
    coordinator.data = {}

    device_info = coordinator.device_info(
        "entry", "USB-DALI2", 5, input_device=input_device
    )

    assert device_info["name"] == expected_name
    assert device_info["model"] == expected_model
    assert device_info["serial_number"] is None


async def test_device_info_uses_gtin_as_model_id(
    coordinator: DaliCoordinator,
) -> None:
    """Test the GTIN populates model_id only, so it is not shown twice."""
    coordinator.data = {5: DaliGearData(gtin=1234567654321)}

    device_info = coordinator.device_info("entry", "USB-DALI2", 5)

    assert device_info["model"] == "DALI control gear"
    assert device_info["model_id"] == "1234567654321"


@pytest.mark.parametrize(
    ("device_types", "expected_model"),
    [
        pytest.param(
            (6,),
            "DALI control gear – LED module (DT6, IEC 62386-207)",
            id="led",
        ),
        pytest.param(
            (1,),
            "DALI control gear – Self-contained emergency lighting "
            "(DT1, IEC 62386-202)",
            id="emergency",
        ),
        pytest.param(
            (6, 8),
            "DALI control gear – LED module (DT6, IEC 62386-207), "
            "Colour control (DT8, IEC 62386-209)",
            id="led_and_colour",
        ),
    ],
)
async def test_device_info_model_exposes_gear_device_type(
    coordinator: DaliCoordinator,
    device_types: tuple[int, ...],
    expected_model: str,
) -> None:
    """Test a gear's model names its device types and IEC 62386 parts."""
    coordinator.data = {5: DaliGearData(device_types=device_types)}

    device_info = coordinator.device_info("entry", "USB-DALI2", 5)

    assert device_info["model"] == expected_model


async def test_device_info_model_exposes_control_device_part(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a control device's model names its instance-type IEC parts."""
    fake_dali_driver.dev_inst_map.mapping = {(5, 0): 4, (5, 1): 3}
    coordinator = DaliCoordinator(
        hass, fake_dali_driver, MockConfigEntry(domain=DOMAIN)
    )
    coordinator.data = {}

    device_info = coordinator.device_info("entry", "USB-DALI2", 5, input_device=True)

    assert device_info["model"] == (
        "DALI control device – Occupancy sensor (IEC 62386-303), "
        "Light sensor (IEC 62386-304)"
    )


async def test_device_info_uses_short_usb_label(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the device name is derived from the USB unique ID, not the title."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="17B5:0201_000000024994_Lunatone_DALI-2 USB PS",
        title="DALI-2 USB PS - /dev/serial/by-id/usb-Lunatone_DALI-2_USB_PS - Lunatone",
    )
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)
    coordinator.data = {}

    device_info = coordinator.device_info(entry.entry_id, entry.title, 0)

    assert device_info["name"] == "DALI-2_USB_PS_000000024994/0 (gear)"


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
    assert sensor.quantity_name == 26
    assert sensor.unit_of_measurement == 9
    assert sensor.resolution == 9
    assert sensor.magnitude == 127
    assert sensor.input_signal_signed is False
    assert sensor.gtin == 1234567654321


async def test_part_306_instance_configured_for_device_instance_scheme(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a discovered Part 306 instance is switched to the Device/Instance scheme.

    Instance-scheme events carry no short address, so the coordinator cannot
    attribute a measured value to a device; the instance is reconfigured to the
    Device/Instance scheme (and its measured-value events re-enabled) on
    discovery.
    """
    fake_dali_driver.dev_inst_map.mapping = {(1, 2): 6}
    coordinator = DaliCoordinator(
        hass, fake_dali_driver, MockConfigEntry(domain=DOMAIN)
    )

    await coordinator._async_update_data()

    scheme_commands = [
        command
        for command in fake_dali_driver.sent
        if isinstance(command, SetEventScheme)
    ]
    assert scheme_commands
    assert any(isinstance(command, EnableInstance) for command in fake_dali_driver.sent)
    assert any(isinstance(command, SetEventFilter) for command in fake_dali_driver.sent)
    scheme_index = fake_dali_driver.sent.index(scheme_commands[0])
    preceding_dtr0 = next(
        command
        for command in reversed(fake_dali_driver.sent[:scheme_index])
        if isinstance(command, DeviceDTR0)
    )
    assert preceding_dtr0.param == EventScheme.device_instance


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


async def test_missing_device_short_address_query_failure(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a failed device missing-short-address query becomes an update failure."""
    with (
        patch.object(fake_dali_driver, "send", side_effect=OSError("offline")),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._has_unaddressed_control_devices()


async def test_assign_device_short_addresses_discovers_new_sensor(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test device commissioning addresses a device and folds it into the map."""
    fake_dali_driver.unaddressed_devices = [[(0, 6)]]
    coordinator = DaliCoordinator(
        hass, fake_dali_driver, MockConfigEntry(domain=DOMAIN)
    )

    remaining = await coordinator.async_assign_control_device_short_addresses()

    assert remaining is False
    assert (0, 0, 6) in coordinator.device_instances
    assert (0, 0) in coordinator.input_devices


async def test_startup_scan_discovers_control_devices(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test control devices are enumerated on the first (startup) full scan."""
    assert (0, 0) not in coordinator.input_devices
    fake_dali_driver.pending_device_instances = {(0, 0): 6}

    await coordinator._async_update_data()

    assert (0, 0, 6) in coordinator.device_instances
    assert (0, 0) in coordinator.input_devices


async def test_periodic_scan_does_not_run_quiescent_discovery(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test later full scans skip autodiscover so the bus is not put quiescent."""
    await coordinator._async_update_data()
    # A device addressed after startup is not folded in by the periodic poll;
    # only startup, the search button, or commissioning run autodiscover.
    coordinator._last_full_scan = None
    fake_dali_driver.pending_device_instances = {(7, 0): 6}

    await coordinator._async_update_data()

    assert (7, 0) not in fake_dali_driver.dev_inst_map.mapping
    assert (7, 0) not in coordinator.input_devices


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
