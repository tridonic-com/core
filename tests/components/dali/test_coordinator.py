"""Test the Tridonic DALI coordinator."""

from types import GeneratorType, SimpleNamespace
from unittest.mock import patch

from dali.exceptions import DALISequenceError, TransmissionError
from dali.gear.general import QueryControlGearPresent
import pytest

from homeassistant.components.dali.const import (
    CONF_BUS_POWER,
    DOMAIN,
    bus_overload_issue_id,
    bus_voltage_error_issue_id,
)
from homeassistant.components.dali.coordinator import (
    DaliCoordinator,
    DaliGearData,
    _dali_level_to_brightness,
)
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
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


async def test_gear_config_min_max_is_decoded(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the min/max dimming levels are read for a gear."""
    gear = await coordinator._query_gear(0)

    assert gear is not None
    assert gear.min_level == 86
    assert gear.max_level == 254


async def test_invalid_max_level_is_discarded(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test an out-of-range maximum level is reported as no value."""
    fake_dali_driver.max_levels[0] = 255

    gear = await coordinator._query_gear(0)

    assert gear is not None
    assert gear.max_level is None


async def test_linear_dimming_curve_is_read(
    coordinator: DaliCoordinator, fake_dali_driver: FakeDaliDriver
) -> None:
    """Test the DT6 dimming curve selects the linear mapping."""
    gear = await coordinator._query_gear(0)

    assert gear is not None
    assert gear.linear_dimming is True


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


async def test_device_info_for_unknown_address(
    coordinator: DaliCoordinator,
) -> None:
    """Test device info falls back to defaults for an unknown gear."""
    coordinator.data = {}

    device_info = coordinator.device_info("entry", "USB-DALI2", 5)

    assert device_info["name"] == "USB-DALI2/5 (gear)"
    assert device_info["model"] == "DALI control gear (address 5)"
    assert device_info["serial_number"] is None


async def test_device_info_uses_gtin_as_model_id(
    coordinator: DaliCoordinator,
) -> None:
    """Test the GTIN populates model_id only, so it is not shown twice."""
    coordinator.data = {5: DaliGearData(gtin=1234567654321)}

    device_info = coordinator.device_info("entry", "USB-DALI2", 5)

    assert device_info["model"] == "DALI control gear (address 5)"
    assert device_info["model_id"] == "1234567654321"


async def test_device_info_reports_the_serial_number(
    coordinator: DaliCoordinator,
) -> None:
    """Test the gear's serial number reaches the registry device."""
    coordinator.data = {5: DaliGearData(serial_no="080706")}

    device_info = coordinator.device_info("entry", "USB-DALI2", 5)

    assert device_info["serial_number"] == "080706"


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
