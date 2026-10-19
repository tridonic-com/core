"""Test the Tridonic DALI coordinator."""

from collections.abc import Callable
from types import GeneratorType, SimpleNamespace
from typing import Any
from unittest.mock import patch

from dali.address import DeviceShort, GearShort
from dali.device.general import (
    DTR0 as DeviceDTR0,
    EnableInstance,
    EventScheme,
    QueryResolution,
    SetEventFilter,
    SetEventScheme,
)
from dali.exceptions import DALISequenceError, TransmissionError
from dali.gear.colour import QueryColourValueDTR
from dali.gear.general import QueryControlGearPresent, QueryFadeTimeFadeRate
from dali.gear.led import QueryDimmingCurve
from dali.memory import (
    diagnostics as diagnostics_memory,
    emergency as emergency_memory,
    info,
    oem,
)
from dali.memory.location import FlagValue
from dali.sequences import progress
import pytest

from homeassistant.components.dali.const import (
    CONF_BUS_POWER,
    DOMAIN,
    address_collision_issue_id,
    bus_overload_issue_id,
    bus_voltage_error_issue_id,
)
from homeassistant.components.dali.coordinator import (
    DIAGNOSTICS_SCAN_INTERVAL,
    DaliCoordinator,
    DaliGearData,
    _dali_level_to_brightness,
    _log_commissioning_progress,
)
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from .conftest import FakeDaliDriver, _FakeGear

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
    original_send = fake_dali_driver.send

    async def send(
        msg: object, *, priority: int = 5, progress: object = None
    ) -> object:
        if isinstance(msg, GeneratorType) and msg.__name__ == "read_all":
            msg.close()
            raise DALISequenceError
        return await original_send(msg, priority=priority, progress=progress)

    with patch.object(fake_dali_driver, "send", side_effect=send):
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


async def test_update_bus_voltage_error_raises_overload_repair(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test a bus sagging under load mid-operation raises the overload repair."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0", CONF_BUS_POWER: True}
    )
    entry.add_to_hass(hass)
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)

    with (
        patch.object(fake_dali_driver, "send", side_effect=TransmissionError(1)),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()

    assert issue_registry.async_get_issue(DOMAIN, bus_overload_issue_id(entry.entry_id))
    assert not issue_registry.async_get_issue(
        DOMAIN, bus_voltage_error_issue_id(entry.entry_id)
    )


async def test_update_recovers_and_clears_bus_voltage_repair(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test a bus that recovers clears the repair raised while it was down."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0", CONF_BUS_POWER: True}
    )
    entry.add_to_hass(hass)
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)

    with (
        patch.object(fake_dali_driver, "send", side_effect=TransmissionError(1)),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()
    assert issue_registry.async_get_issue(DOMAIN, bus_overload_issue_id(entry.entry_id))

    await coordinator._async_update_data()

    assert not issue_registry.async_get_issue(
        DOMAIN, bus_overload_issue_id(entry.entry_id)
    )


async def test_metadata_success(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test bank metadata is copied into gear data."""
    gear = await coordinator._query_metadata(GearShort(0))

    assert gear.gtin == 1234567654321
    assert gear.identification_number == 1
    assert gear.firmware_version == "1.0"
    assert gear.hardware_version == "2.1"


@pytest.mark.parametrize(
    ("identification_number", "serial_no", "batch_no"),
    [
        (0x0102030405060708, "080706", "0504030201"),
        (0, "000000", "0000000000"),
        (None, None, None),
    ],
    ids=["distinct_bytes", "zero", "not_implemented"],
)
async def test_identification_number_splits_into_serial_and_batch(
    coordinator: DaliCoordinator,
    fake_dali_driver: FakeDaliDriver,
    identification_number: int | None,
    serial_no: str | None,
    batch_no: str | None,
) -> None:
    """Test the identification number is split into its two printed numbers."""
    fake_dali_driver.metadata[info.IdentificationNumber] = identification_number

    gear = await coordinator._query_metadata(GearShort(0))

    assert gear.serial_no == serial_no
    assert gear.batch_no == batch_no


@pytest.mark.parametrize(
    ("gtin", "version", "expected"),
    [
        (9006210123456, "18.5", "4.2-5"),
        (9006210123456, "0.0", "0.0-0"),
        (1234567654321, "18.5", "18.5"),
        (None, "18.5", "18.5"),
    ],
    ids=["tridonic", "tridonic_zero", "other_manufacturer", "no_gtin"],
)
async def test_tridonic_firmware_version_carries_a_build_number(
    coordinator: DaliCoordinator,
    fake_dali_driver: FakeDaliDriver,
    gtin: int | None,
    version: str,
    expected: str,
) -> None:
    """Test Tridonic gear reports its version as major.minor-build.

    Their first version byte packs major and minor the way IEC 62386-102 packs
    a single-byte version, leaving the second byte to be the build number.
    """
    fake_dali_driver.metadata[info.GTIN] = gtin
    fake_dali_driver.metadata[info.FirmwareVersion] = version

    gear = await coordinator._query_metadata(GearShort(0))

    assert gear.firmware_version == expected


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
    assert data[1].emergency.status is not None
    assert data[1].emergency.status.battery_fully_charged
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
        "version": 3,
        "data": {
            "gear": {
                "0": {
                    "gtin": 42,
                    "identification_number": 7,
                    "serial_no": "070000",
                    "batch_no": "0000000000",
                    "firmware_version": "9.9",
                    "hardware_version": "8.8",
                }
            },
            "device": {"3": {"identification_number": 11}},
            "luminaire": {"0": {"luminaire_identification": "Office downlight"}},
            "maintenance": {"0": {"rated_median_useful_life": 50000}},
            "sensor": {"3:0": {"quantity_name": 1, "resolution": 9}},
        },
    }
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)

    await coordinator.async_load_metadata_cache()

    assert coordinator._metadata[0].gtin == 42
    assert coordinator._device_metadata[3].identification_number == 11
    assert coordinator._luminaire[0].luminaire_identification == "Office downlight"
    assert coordinator._maintenance[0].rated_median_useful_life == 50000
    assert coordinator.input_devices[(3, 0)].resolution == 9
    # The fast initial pass serves the cached value without a bank-0 read, so
    # the gear keeps the restored GTIN rather than the one the bus would report.
    gear = await coordinator._query_gear(0, fast=True)
    assert gear is not None
    assert gear.gtin == 42


@pytest.mark.parametrize("version", [1, 2], ids=["version_1", "version_2"])
async def test_outdated_metadata_cache_is_discarded(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    hass_storage: dict[str, Any],
    version: int,
) -> None:
    """Test a cache written before the stored fields changed is dropped.

    Version 1 firmware versions carry the old format and have no serial or
    batch number, and version 2 predates the last memory bank the extended
    bank reads are gated on. Nothing re-reads a device that is already cached,
    so a stale entry would never be corrected.
    """
    entry = MockConfigEntry(domain=DOMAIN)
    hass_storage[f"{DOMAIN}.{entry.entry_id}_metadata"] = {
        "version": version,
        "data": {"gear": {"0": {"gtin": 42, "firmware_version": "9.9"}}},
    }
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)

    await coordinator.async_load_metadata_cache()

    assert coordinator._metadata == {}


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
    coordinator.async_set_updated_data(await coordinator._async_update_data())

    with patch.object(fake_dali_driver, "send", side_effect=OSError("offline")):
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
    original_send = fake_dali_driver.send

    async def send(
        msg: object, *, priority: int = 5, progress: object = None
    ) -> object:
        if isinstance(msg, QueryDimmingCurve):
            return None
        return await original_send(msg, priority=priority, progress=progress)

    fake_dali_driver.color_features = {2: None}
    with patch.object(fake_dali_driver, "send", side_effect=send):
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
    assert emergency.mode is not None and emergency.mode.normal_mode
    assert emergency.features is not None
    assert emergency.features.integral_emergency_control_gear
    assert not emergency.features.maintained_control_gear
    assert emergency.status is not None and emergency.status.battery_fully_charged
    assert emergency.failure_status is not None
    assert not emergency.failure_status.status
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
    original_send = fake_dali_driver.send

    async def send(
        msg: object, *, priority: int = 5, progress: object = None
    ) -> object:
        if (
            isinstance(msg, GeneratorType)
            and msg.__name__ == "read_all"
            and msg.gi_frame.f_locals.get("self").address == 208
        ):
            msg.close()
            raise DALISequenceError
        return await original_send(msg, priority=priority, progress=progress)

    with patch.object(fake_dali_driver, "send", side_effect=send):
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
        pytest.param(
            False, "USB-DALI2/5 (gear)", "DALI control gear (address 5)", id="gear"
        ),
        pytest.param(
            True,
            "USB-DALI2/5 (sensor)",
            "DALI control device (address 5)",
            id="sensor",
        ),
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

    assert device_info["model"] == "DALI control gear (address 5)"
    assert device_info["model_id"] == "1234567654321"


@pytest.mark.parametrize(
    ("serial_no", "batch_no", "expected_serial_number"),
    [
        pytest.param("080706", "0504030201", "0504030201-080706", id="both"),
        pytest.param("080706", None, "080706", id="serial_only"),
        pytest.param(None, "0504030201", "0504030201", id="batch_only"),
        pytest.param(None, None, None, id="neither"),
    ],
)
async def test_device_info_joins_the_batch_and_serial_number(
    coordinator: DaliCoordinator,
    serial_no: str | None,
    batch_no: str | None,
    expected_serial_number: str | None,
) -> None:
    """Test the registry's single serial number field carries both halves."""
    coordinator.data = {5: DaliGearData(serial_no=serial_no, batch_no=batch_no)}

    device_info = coordinator.device_info("entry", "USB-DALI2", 5)

    assert device_info["serial_number"] == expected_serial_number


@pytest.mark.parametrize(
    ("device_types", "expected_model"),
    [
        pytest.param(
            (6,),
            "DALI control gear – LED module (DT6, IEC 62386-207) (address 5)",
            id="led",
        ),
        pytest.param(
            (1,),
            "DALI control gear – Self-contained emergency lighting "
            "(DT1, IEC 62386-202) (address 5)",
            id="emergency",
        ),
        pytest.param(
            (6, 8),
            "DALI control gear – LED module (DT6, IEC 62386-207), "
            "Colour control (DT8, IEC 62386-209) (address 5)",
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
        "Light sensor (IEC 62386-304) (address 5)"
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


async def test_non_sensor_instance_switched_to_device_instance_scheme(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a light/button instance is also addressed, but gets no sensor filter.

    Light (type 4), occupancy and push-button instances emit events the
    entities key by short address, so they too are switched to the
    Device/Instance scheme. Only general sensors need the measured-value
    filter, so it must not be sent for them.
    """
    fake_dali_driver.dev_inst_map.mapping = {(2, 0): 4}
    coordinator = DaliCoordinator(
        hass, fake_dali_driver, MockConfigEntry(domain=DOMAIN)
    )

    await coordinator._async_update_data()

    assert any(isinstance(command, SetEventScheme) for command in fake_dali_driver.sent)
    assert not any(
        isinstance(command, SetEventFilter) for command in fake_dali_driver.sent
    )


async def test_part_306_query_failure_raises_update_failed(
    hass: HomeAssistant, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test Part 306 query failures become coordinator update failures."""
    coordinator = DaliCoordinator(
        hass, fake_dali_driver, MockConfigEntry(domain=DOMAIN)
    )

    original_send = fake_dali_driver.send

    async def send(
        msg: object, *, priority: int = 5, progress: object = None
    ) -> object:
        # Fail only the bare Part 306 sensor queries, not the memory-bank read.
        if not isinstance(msg, GeneratorType):
            raise OSError("offline")
        return await original_send(msg, priority=priority, progress=progress)

    with (
        patch.object(fake_dali_driver, "send", side_effect=send),
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


async def test_gear_config_is_decoded(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the configuration variables are decoded from their DALI encodings."""
    gear = await coordinator._query_gear(0)

    assert gear is not None
    assert gear.min_level == 86
    assert gear.max_level == 254
    assert gear.system_failure_level == 200
    assert gear.fade_time == 4
    assert gear.fade_rate == 7
    assert gear.extended_fade_time_base == 5
    assert gear.extended_fade_time_multiplier == 3


async def test_framing_errors_leave_the_fade_settings_unset(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a framing error on either fade query reads as no value.

    The library's convenience properties decode their nibbles out of an errored
    frame too, so a framing error would otherwise surface as a plausible code.
    """
    fake_dali_driver.fade[0] = None
    fake_dali_driver.extended_fade[0] = None

    gear = await coordinator._query_gear(0)

    assert gear is not None
    assert gear.fade_time is None
    assert gear.fade_rate is None
    assert gear.extended_fade_time_base is None
    assert gear.extended_fade_time_multiplier is None


async def test_invalid_max_level_is_discarded(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test an out-of-range maximum level is reported as no value."""
    fake_dali_driver.max_levels[0] = 255

    gear = await coordinator._query_gear(0)

    assert gear is not None
    assert gear.max_level is None


async def test_mask_system_failure_level_is_kept(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the MASK answer is decoded as level 255, not discarded.

    The library reports it as a string, which the numeric decoder drops, yet it
    is a real stored value meaning the gear holds its level on a bus failure.
    """
    del fake_dali_driver.system_failure_levels[0]

    gear = await coordinator._query_gear(0)

    assert gear is not None
    assert gear.system_failure_level == 255


async def test_gear_config_is_cached_between_full_scans(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the configuration variables are only re-read on a full scan."""
    await coordinator._async_update_data()
    fake_dali_driver.sent.clear()

    await coordinator._async_update_data()

    assert not any(
        isinstance(command, QueryFadeTimeFadeRate) for command in fake_dali_driver.sent
    )

    coordinator._last_full_scan = None
    await coordinator._async_update_data()

    assert any(
        isinstance(command, QueryFadeTimeFadeRate) for command in fake_dali_driver.sent
    )


async def test_async_update_gear_config_forces_a_re_read(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a write-triggered refresh re-reads the cached configuration."""
    await coordinator._async_update_data()
    fake_dali_driver.min_levels[0] = 120

    await coordinator.async_update_gear(0)
    assert coordinator.data[0].min_level == 86

    await coordinator.async_update_gear_config(0)
    assert coordinator.data[0].min_level == 120


async def test_gear_config_cache_is_pruned_on_full_scan(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a re-addressed gear cannot inherit the previous occupant's config."""
    await coordinator._async_update_data()
    assert 0 in coordinator._gear_config

    del fake_dali_driver.gears[0]
    coordinator._last_full_scan = None
    await coordinator._async_update_data()

    assert 0 not in coordinator._gear_config


def _count_bank_reads(
    driver: FakeDaliDriver, bank_address: int, gear_address: int = 0
) -> Callable[[], int]:
    """Return a counter of the reads of one memory bank of one gear.

    Memory banks are read through a sequence rather than a single command, so
    they leave no trace in the recorded command list.
    """
    original_send = driver.send
    reads = 0

    def _is_read(msg: object) -> bool:
        if not isinstance(msg, GeneratorType) or msg.__name__ != "read_all":
            return False
        frame_locals = msg.gi_frame.f_locals
        addr = frame_locals["addr"]
        return (
            frame_locals["self"].address == bank_address
            and getattr(addr, "address", addr) == gear_address
        )

    async def send(
        msg: object, *, priority: int = 5, progress: object = None
    ) -> object:
        nonlocal reads
        if _is_read(msg):
            reads += 1
        return await original_send(msg, priority=priority, progress=progress)

    driver.send = send  # type: ignore[method-assign]
    return lambda: reads


async def _refresh_through_enrichment(coordinator: DaliCoordinator) -> None:
    """Run the refresh sequence the integration performs when it is set up.

    The first pass is the fast one that skips every slow memory bank read;
    enrichment then reads the static banks and refreshes again behind them.
    """
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    await coordinator.async_enrich_deferred_data()


async def test_extended_memory_banks_are_decoded(
    coordinator: DaliCoordinator,
) -> None:
    """Test the DiiA Part 251, 252 and 253 banks reach the gear data."""
    await _refresh_through_enrichment(coordinator)

    gear = coordinator.data[0]
    assert gear.luminaire is not None
    assert gear.luminaire.manufacturer_gtin == 4048879000000
    assert gear.luminaire.cri == 90
    assert gear.luminaire.luminaire_identification == "Office downlight"
    assert gear.maintenance is not None
    assert gear.maintenance.rated_median_useful_life == 50000
    assert gear.diagnostics is not None
    assert gear.diagnostics.control_gear_start_counter == 120
    assert gear.diagnostics.control_gear_thermal_derating is True
    assert gear.diagnostics.light_source_temperature == 52
    # The scaled values arrive as Decimals and must not stay Decimals: neither
    # the metadata store nor the diagnostics dump can serialize them.
    assert gear.diagnostics.control_gear_external_supply_voltage == 230.5
    assert isinstance(gear.diagnostics.control_gear_external_supply_voltage, float)
    assert gear.active_energy == 1234.5


async def test_gear_without_the_extended_banks_reports_nothing(
    coordinator: DaliCoordinator,
) -> None:
    """Test a gear answering no extended bank leaves every field unset."""
    await _refresh_through_enrichment(coordinator)

    gear = coordinator.data[1]
    assert gear.luminaire is None
    assert gear.maintenance is None
    assert gear.diagnostics is None
    assert gear.active_energy is None


async def test_bank_1_without_the_part_251_extension(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test only the two IEC 62386-102 values are taken without Part 251.

    The remaining locations of the bank are whatever the OEM stored there when
    the content format identifier does not declare the extension.
    """
    fake_dali_driver.bank_memory[1][0][oem.ContentFormatID] = 0x0001

    await _refresh_through_enrichment(coordinator)

    luminaire = coordinator.data[0].luminaire
    assert luminaire is not None
    assert luminaire.manufacturer_gtin == 4048879000000
    assert luminaire.luminaire_id == 77
    assert luminaire.cri is None
    assert luminaire.luminaire_identification is None


@pytest.mark.parametrize(
    "flag",
    [FlagValue.MASK, FlagValue.TMASK],
    ids=["mask", "tmask"],
)
async def test_unavailable_memory_values_are_dropped(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver, flag: FlagValue
) -> None:
    """Test a bank answering MASK or TMASK reports no reading at all.

    Both flags are enum members that would otherwise end up verbatim in an
    entity state, so they are checked on the emergency bank too.
    """
    fake_dali_driver.bank_memory[205][0][diagnostics_memory.ControlGearTemperature] = (
        flag
    )
    fake_dali_driver.emergency_memory[1][emergency_memory.ControlGearTemperature] = flag

    await _refresh_through_enrichment(coordinator)

    diagnostics = coordinator.data[0].diagnostics
    assert diagnostics is not None
    assert diagnostics.control_gear_internal_temperature is None
    emergency = coordinator.data[1].emergency
    assert emergency is not None
    assert emergency.control_gear_temperature is None


async def test_last_memory_bank_rules_out_the_extended_banks(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a gear reporting a low last memory bank is not read any further."""
    fake_dali_driver.metadata[info.LastMemoryBank] = 0
    reads = _count_bank_reads(fake_dali_driver, 205)

    await _refresh_through_enrichment(coordinator)

    assert reads() == 0
    assert coordinator.data[0].diagnostics is None
    assert coordinator.data[0].luminaire is None


async def test_volatile_banks_are_read_on_their_own_interval(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the Part 252 and 253 banks are not re-read on every poll."""
    reads = _count_bank_reads(fake_dali_driver, 205)

    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert reads() == 0

    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert reads() == 1

    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert reads() == 1

    coordinator._last_diagnostics_scan -= DIAGNOSTICS_SCAN_INTERVAL.total_seconds()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert reads() == 2


async def test_static_banks_are_read_once(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the luminaire bank is not re-read for a gear already cached."""
    reads = _count_bank_reads(fake_dali_driver, 1)

    await _refresh_through_enrichment(coordinator)
    assert reads() == 1

    await coordinator.async_enrich_deferred_data()
    assert reads() == 1


async def test_diagnostics_caches_are_pruned_on_full_scan(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a re-addressed gear cannot inherit the previous occupant's readings."""
    await _refresh_through_enrichment(coordinator)
    assert 0 in coordinator._diagnostics
    assert 0 in coordinator._energy

    del fake_dali_driver.gears[0]
    coordinator._last_full_scan = None
    coordinator.async_set_updated_data(await coordinator._async_update_data())

    assert 0 not in coordinator._diagnostics
    assert 0 not in coordinator._energy


async def test_static_bank_values_are_persisted(coordinator: DaliCoordinator) -> None:
    """Test the luminaire and maintenance banks survive a restart."""
    await _refresh_through_enrichment(coordinator)

    stored = coordinator._metadata_snapshot()

    assert stored["luminaire"]["0"]["luminaire_identification"] == "Office downlight"
    assert stored["maintenance"]["0"]["rated_median_useful_life"] == 50000


async def test_detect_address_collision(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test two gear answering one short address are reported as a collision."""
    fake_dali_driver.colliding[5] = [_FakeGear((6,), 100), _FakeGear((6,), 200)]

    assert await coordinator._detect_address_collisions() == [5]


async def test_no_collision_without_framing_error(
    coordinator: DaliCoordinator,
) -> None:
    """Test a bus where every address answers cleanly reports no collision."""
    assert await coordinator._detect_address_collisions() == []


async def test_transient_frame_error_is_not_a_collision(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test a single glitched probe frame does not count as a collision."""
    fake_dali_driver.flaky = {0}

    assert await coordinator._detect_address_collisions() == []


async def test_address_collision_raises_repair(
    hass: HomeAssistant,
    fake_dali_driver: FakeDaliDriver,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test a collision found during a full scan raises a fixable repair."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"}, title="USB-DALI2"
    )
    entry.add_to_hass(hass)
    fake_dali_driver.colliding[5] = [_FakeGear((6,), 100), _FakeGear((6,), 200)]
    coordinator = DaliCoordinator(hass, fake_dali_driver, entry)

    await coordinator._async_update_data()

    issue = issue_registry.async_get_issue(
        DOMAIN, address_collision_issue_id(entry.entry_id)
    )
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders["addresses"] == "5"
