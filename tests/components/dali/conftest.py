"""Common fixtures for the Tridonic DALI tests."""

import asyncio
from collections.abc import Generator
from dataclasses import dataclass
from decimal import Decimal
from types import GeneratorType, SimpleNamespace
from typing import TypeVar
from unittest.mock import AsyncMock, patch

from dali.command import Command, Response
from dali.device.general import (
    DTR0 as DeviceDTR0,
    QueryMissingShortAddress as QueryMissingDeviceShortAddress,
)
from dali.exceptions import MemoryLocationNotImplemented
from dali.frame import BackwardFrame
from dali.gear.colour import (
    QueryColourStatus,
    QueryColourTypeFeatures,
    QueryColourValueDTR,
)
from dali.gear.general import (
    DTR0,
    QueryActualLevel,
    QueryControlGearPresent,
    QueryDeviceType,
    QueryExtendedFadeTime,
    QueryFadeTimeFadeRate,
    QueryMaxLevel,
    QueryMinLevel,
    QueryMissingShortAddress,
    QuerySystemFailureLevel,
    SetShortAddress,
)
from dali.gear.led import QueryDimmingCurve
from dali.gear.sequences import (
    EmergencyInformation,
    QueryEmergencyFailureStatusResponse,
    QueryEmergencyFeaturesResponse,
    QueryEmergencyModeResponse,
    QueryEmergencyStatusResponse,
)
from dali.memory import (
    diagnostics as diagnostics_memory,
    emergency as emergency_memory,
    energy,
    info,
    maintenance,
    oem,
)
import pytest

_StatusByte = TypeVar("_StatusByte", bound=Response)


def decode_status(cls: type[_StatusByte], byte: int) -> _StatusByte:
    """Decode a raw status byte into a python-dali query response object."""
    return cls(BackwardFrame(byte))


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "homeassistant.components.dali.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@dataclass
class _FakeGear:
    """A physical control gear tracked through a collision resolution."""

    device_types: tuple[int, ...]
    serial: int


class FakeDevInstMap:
    """Fake device/instance type mapper."""

    def __init__(self) -> None:
        """Initialize an empty mapping."""
        self.mapping: dict[tuple[int, int], int] = {}

    def autodiscover(self, addr: object = None) -> Generator[None]:
        """Re-enumerate devices; the fake folds them in during commissioning."""
        yield from ()


class FakeDaliDriver:
    """Fake python-dali driver for integration tests."""

    def __init__(self, uri: str) -> None:
        """Initialize the fake driver."""
        self.uri = uri
        # Models an interface that is plugged in; tests that need it to go away
        # clear this, as python-dali does when the serial transport is lost.
        self.connected = True
        self.closed = False
        self.sent: list[Command] = []
        self.gears = {0: (6,), 1: (1,), 2: (8,)}
        self.levels = {0: 128, 1: 254, 2: 254}
        self.min_levels = {0: 86, 2: 1}
        self.max_levels: dict[int, int] = {}
        self.physical_min_levels = {0: 86}
        # Gear without an entry answers MASK, the power-up default.
        self.system_failure_levels: dict[int, int] = {0: 200}
        # (fade time code, fade rate code); the factory default is (0, 7), and
        # None models a gear whose answer arrives with a framing error.
        self.fade: dict[int, tuple[int, int] | None] = {0: (4, 7)}
        # (base, multiplier); gear without an entry does not implement it, and
        # None models a framing error.
        self.extended_fade: dict[int, tuple[int, int] | None] = {0: (5, 3)}
        self.gear_writes: list[tuple[int, str, int]] = []
        self.linear_dimming = {0: True}
        self.color_features = {2: (True, True)}
        self.color_tc_active = {2: True}
        self.color_values = {
            QueryColourValueDTR.ColourTemperatureTC: 250,
            QueryColourValueDTR.ColourTemperatureTcPhysicalCoolest: 153,
            QueryColourValueDTR.ColourTemperatureTcPhysicalWarmest: 500,
            QueryColourValueDTR.XCoordinate: 32768,
            QueryColourValueDTR.YCoordinate: 16384,
        }
        self.metadata = {
            info.GTIN: 1234567654321,
            info.IdentificationNumber: 1,
            info.FirmwareVersion: "1.0",
            info.HardwareVersion: "2.1",
            info.LastMemoryBank: 207,
        }
        # DT1 emergency state and recorded commands, keyed by gear address.
        self.emergency_info: dict[int, dict[str, int | None]] = {
            1: {
                "emergency_mode": 0b00000010,
                # Non-maintained (bit 0 only): the gear only lights in an
                # emergency, so no normal-mode light entity is created for it.
                "emergency_features": 0b00000001,
                "emergency_failure_status": 0x00,
                "emergency_status": 0b00001000,
                "battery_charge": 254,
                "emergency_level": 254,
                "duration_test_result": 60,
                "lamp_emergency_time": 5,
                "lamp_total_operation_time": 100,
                "rated_duration": 90,
            }
        }
        self.emergency_memory: dict[int, dict[object, int | float]] = {
            1: {
                emergency_memory.ControlGearTemperature: 25,
                emergency_memory.ControlGearMaxReferenceTemperature: 75,
                emergency_memory.MinControlGearTemperatureTotal: 10,
                emergency_memory.MaxControlGearTemperatureTotal: 80,
                emergency_memory.AveragePowerDuringCharging: 5.0,
                emergency_memory.AveragePowerDuringMaintenance: 1.0,
                emergency_memory.BatteryRechargeTime: 120,
                emergency_memory.FunctionTestTime: 30,
                emergency_memory.BatteryFailureCounter: 0,
                emergency_memory.LampEmergencyTimeTotal: 300,
                emergency_memory.LampEmergencyTimeCurrentBattery: 60,
                emergency_memory.BatteryConnectedTimeTotal: 400,
                emergency_memory.EmergencyModeCounterTotal: 3,
                emergency_memory.RestModeCounter: 1,
                emergency_memory.StartFunctionTestCounter: 12,
                emergency_memory.StartDurationTestCounterTotal: 4,
                emergency_memory.FunctionTestFailedCounterTotal: 0,
                emergency_memory.DurationTestFailedCounterTotal: 0,
            }
        }
        self.emergency_commands: list[tuple[int, str]] = []
        # Every memory bank other than bank 0, keyed bank, then gear address;
        # a gear missing from a bank's entry does not implement that bank.
        self.bank_memory: dict[int, dict[int, dict[object, object]]] = {
            1: {
                0: {
                    oem.ManufacturerGTIN: 4048879000000,
                    oem.LuminaireID: 77,
                    oem.ContentFormatID: 0x0003,
                    oem.YearOfManufacture: 21,
                    oem.WeekOfManufacture: 35,
                    oem.InputPowerNominal: 42,
                    oem.InputPowerMinimumDim: 5,
                    oem.MainsVoltageMinimum: 198,
                    oem.MainsVoltageMaximum: 264,
                    oem.LightOutputNominal: 3600,
                    oem.CRI: 90,
                    oem.CCT: 4000,
                    oem.LightDistributionType: "Type III",
                    oem.LuminaireColor: "white",
                    oem.LuminaireIdentification: "Office downlight",
                }
            },
            202: {0: {energy.ActiveEnergy: Decimal("1234.5")}},
            205: {
                0: {
                    diagnostics_memory.ControlGearOperatingTime: 360000,
                    diagnostics_memory.ControlGearStartCounter: 120,
                    diagnostics_memory.ControlGearExternalSupplyVoltage: Decimal(
                        "230.5"
                    ),
                    diagnostics_memory.ControlGearExternalSupplyVoltageFrequency: 50,
                    diagnostics_memory.ControlGearPowerFactor: Decimal("0.95"),
                    diagnostics_memory.ControlGearTemperature: 45,
                    diagnostics_memory.ControlGearOutputCurrentPercent: 80,
                    diagnostics_memory.ControlGearOverallFailureCondition: False,
                    diagnostics_memory.ControlGearExternalSupplyUndervoltage: False,
                    diagnostics_memory.ControlGearExternalSupplyOvervoltage: False,
                    diagnostics_memory.ControlGearOutputPowerLimitation: False,
                    diagnostics_memory.ControlGearThermalDerating: True,
                    diagnostics_memory.ControlGearThermalShutdown: False,
                }
            },
            206: {
                0: {
                    diagnostics_memory.LightSourceStartCounterResettable: 12,
                    diagnostics_memory.LightSourceStartCounter: 118,
                    diagnostics_memory.LightSourceOnTimeResettable: 7200,
                    diagnostics_memory.LightSourceOnTime: 352800,
                    diagnostics_memory.LightSourceVoltage: Decimal("38.4"),
                    diagnostics_memory.LightSourceCurrent: Decimal("0.35"),
                    diagnostics_memory.LightSourceTemperature: 52,
                    diagnostics_memory.LightSourceOverallFailureCondition: False,
                    diagnostics_memory.LightSourceShortCircuit: False,
                    diagnostics_memory.LightSourceOpenCircuit: False,
                    diagnostics_memory.LightSourceThermalDerating: False,
                    diagnostics_memory.LightSourceThermalShutdown: False,
                }
            },
            207: {
                0: {
                    maintenance.RatedMedianUsefulLifeOfLuminaire: 50000,
                    maintenance.InternalControlGearReferenceTemperature: 65,
                    maintenance.RatedMedianUsefulLightSourceStarts: 50000,
                }
            },
            208: self.emergency_memory,
        }
        self._transport = SimpleNamespace(close=self._close)
        # Per-gear bank-0 identification overriding the shared default, so gear
        # re-addressed out of a collision keep distinct serial numbers.
        self.gear_metadata: dict[int, dict[object, object]] = {}
        # Short addresses answered by more than one gear, each with its own
        # identity; a gear here garbles the collision-probe backward frame.
        self.colliding: dict[int, list[_FakeGear]] = {}
        # Gear whose short address was cleared, awaiting re-commissioning.
        self._pool: list[_FakeGear] = []
        # Addresses that glitch one probe frame but are not truly colliding.
        self.flaky: set[int] = set()
        self._flaky_seen: set[int] = set()
        self._gear_dtr0: int | None = None
        self._commission_available: list[int] | None = None
        self.unaddressed_gears: list[tuple[int, ...]] = []
        self.dev_inst_map = FakeDevInstMap()
        self.event_queue: asyncio.Queue[object] = asyncio.Queue()
        # Each pending control device is a list of (instance, instance_type) tuples.
        self.unaddressed_devices: list[list[tuple[int, int]]] = []
        # Instances an autodiscover run folds in, keyed by (address, instance),
        # modelling a control device a later scan picks up.
        self.pending_device_instances: dict[tuple[int, int], int] = {}

    async def connect(self, *, scan_dev_inst: bool = False) -> None:
        """Connect to the fake interface."""
        self.connected = True

    @property
    def is_connected(self) -> bool:
        """Return whether the fake interface is still reachable."""
        return self.connected

    def new_dali_rx_queue(self) -> asyncio.Queue[object]:
        """Return the fake DALI event queue."""
        return self.event_queue

    async def send(
        self, msg: Command | Generator, *, priority: int = 5, progress=None
    ) -> object:
        """Return a response for a fake command, or run a fake sequence."""
        if isinstance(msg, GeneratorType):
            return await self._run_sequence(msg, progress=progress)
        return await self._send_command(msg)

    async def run_sequence(self, sequence: Generator, progress=None) -> object:
        """Run a fake sequence (deprecated alias kept for API parity)."""
        return await self._run_sequence(sequence, progress=progress)

    async def _send_command(self, command: Command) -> SimpleNamespace:
        """Return a response for a fake DALI command."""
        self.sent.append(command)
        if isinstance(command, DeviceDTR0):
            return SimpleNamespace(value=None)
        if isinstance(command, QueryMissingShortAddress):
            return SimpleNamespace(value=True if self.unaddressed_gears else None)
        if isinstance(command, QueryMissingDeviceShortAddress):
            return SimpleNamespace(value=True if self.unaddressed_devices else None)
        if isinstance(command, DTR0):
            self._gear_dtr0 = command.param
            return SimpleNamespace(value=None)
        address = command.destination.address
        if isinstance(command, SetShortAddress):
            self._set_short_address(address, self._gear_dtr0)
            return SimpleNamespace(value=None)
        if isinstance(command, QueryControlGearPresent):
            return self._gear_present_response(address)
        if isinstance(command, QueryActualLevel):
            return SimpleNamespace(value=self.levels[address])
        if isinstance(command, QueryMinLevel):
            return SimpleNamespace(value=self.min_levels.get(address, 1))
        if isinstance(command, QueryMaxLevel):
            return SimpleNamespace(value=self.max_levels.get(address, 254))
        if isinstance(command, QuerySystemFailureLevel):
            # NumericResponseMask reports 255 as a string, not as a number.
            level = self.system_failure_levels.get(address, 255)
            return SimpleNamespace(value="MASK" if level == 255 else level)
        if isinstance(command, QueryFadeTimeFadeRate):
            fade = self.fade.get(address, (0, 7))
            if fade is None:
                return SimpleNamespace(value="(framing error)")
            fade_time, fade_rate = fade
            return SimpleNamespace(value=(fade_time << 4) | fade_rate)
        if isinstance(command, QueryExtendedFadeTime):
            # The real command answers with a bare Response, which exposes the
            # backward frame rather than a numeric value.
            if address not in self.extended_fade:
                return SimpleNamespace(raw_value=None)
            extended = self.extended_fade[address]
            if extended is None:
                return SimpleNamespace(raw_value=SimpleNamespace(error=True))
            base, multiplier = extended
            return SimpleNamespace(
                raw_value=SimpleNamespace(
                    error=False, as_integer=(multiplier << 4) | base
                )
            )
        if isinstance(command, QueryColourTypeFeatures):
            features = self.color_features.get(address)
            if features is None:
                return SimpleNamespace(raw_value=None)
            tc_capable, xy_capable = features
            return SimpleNamespace(
                raw_value=SimpleNamespace(error=False),
                Tc_capable=tc_capable,
                xy_capable=xy_capable,
            )
        if isinstance(command, QueryColourStatus):
            tc_active = self.color_tc_active.get(address, False)
            return SimpleNamespace(
                raw_value=SimpleNamespace(error=False),
                colour_type_colour_temperature_Tc_active=1 if tc_active else 0,
            )
        if isinstance(command, QueryDimmingCurve):
            value = 1 if self.linear_dimming.get(address, False) else 0
            return SimpleNamespace(raw_value=SimpleNamespace(as_integer=value))
        if type(command).__module__ == "dali.gear.emergency":
            command_name = type(command).__name__
            self.emergency_commands.append((address, command_name))
            self._apply_emergency_command(address, command_name)
            return SimpleNamespace(value=None)
        return SimpleNamespace(value=None)

    async def _run_sequence(self, sequence, progress=None):
        """Return a fake sequence response."""
        if sequence.__name__ == "autodiscover":
            sequence.close()
            self.dev_inst_map.mapping.update(self.pending_device_instances)
            self.pending_device_instances = {}
            return None
        if sequence.__name__ == "Commissioning":
            # Gear and device commissioning share the generator name; the
            # defining module distinguishes which address space it targets.
            is_device = (
                sequence.gi_frame is not None
                and sequence.gi_frame.f_globals["__name__"] == "dali.device.sequences"
            )
            available = (
                sequence.gi_frame.f_locals.get("available_addresses")
                if sequence.gi_frame is not None
                else None
            )
            sequence.close()
            # Commissioning takes bus time on real hardware.
            await asyncio.sleep(0)
            if is_device:
                return self._commission_devices()
            self._commission_available = available
            return self._commission()
        if sequence.__name__ == "read_all":
            frame_locals = sequence.gi_frame.f_locals
            bank = frame_locals.get("self")
            addr = frame_locals.get("addr")
            address = getattr(addr, "address", addr)
            sequence.close()
            bank_address = getattr(bank, "address", None)
            if bank_address == 0:
                return self.gear_metadata.get(address, self.metadata)
            values = self.bank_memory.get(bank_address, {})
            if address not in values:
                raise MemoryLocationNotImplemented
            return dict(values[address])
        if sequence.__name__ == "set_dtr0_variable":
            frame_locals = sequence.gi_frame.f_locals
            address = getattr(
                frame_locals.get("address"), "address", frame_locals.get("address")
            )
            command_class = frame_locals["command"]
            value = frame_locals["value"]
            sequence.close()
            self.gear_writes.append((address, command_class.__name__, value))
            self._apply_gear_write(address, command_class.__name__, value)
            return None
        if sequence.__name__ == "QueryEmergencyInformation":
            frame_locals = sequence.gi_frame.f_locals
            address = getattr(
                frame_locals.get("address"), "address", frame_locals.get("address")
            )
            sequence.close()
            info_ = dict(self.emergency_info[address])
            return EmergencyInformation(
                emergency_mode=decode_status(
                    QueryEmergencyModeResponse, info_.pop("emergency_mode")
                ),
                emergency_features=decode_status(
                    QueryEmergencyFeaturesResponse, info_.pop("emergency_features")
                ),
                emergency_failure_status=decode_status(
                    QueryEmergencyFailureStatusResponse,
                    info_.pop("emergency_failure_status"),
                ),
                emergency_status=decode_status(
                    QueryEmergencyStatusResponse, info_.pop("emergency_status")
                ),
                **info_,
            )
        first_command = next(sequence)
        if isinstance(first_command, QueryDeviceType):
            return list(self.gears.get(first_command.destination.address, ()))
        if isinstance(first_command, QueryActualLevel):
            dtr_command = next(sequence)
            assert isinstance(dtr_command, DTR0)
            return self.color_values.get(dtr_command.param)
        return self.metadata

    def _apply_gear_write(self, address: int, command_name: str, value: int) -> None:
        """Mirror how a gear arbitrates a written configuration variable."""
        fade_time, fade_rate = self.fade.get(address) or (0, 7)
        if command_name == "SetMinLevel":
            physical_min = self.physical_min_levels.get(address, 1)
            self.min_levels[address] = min(
                max(value, physical_min), self.max_levels.get(address, 254)
            )
        elif command_name == "SetMaxLevel":
            self.max_levels[address] = max(value, self.min_levels.get(address, 1))
        elif command_name == "SetSystemFailureLevel":
            # Stored verbatim; the gear only applies the limits when it fails.
            self.system_failure_levels[address] = value
        elif command_name == "SetFadeTime":
            self.fade[address] = (value & 0x0F, fade_rate)
        elif command_name == "SetFadeRate":
            self.fade[address] = (fade_time, value & 0x0F)
        elif command_name == "SetExtendedFadeTime":
            # A DTR0 above 0x4f selects a reserved multiplier and resets both.
            self.extended_fade[address] = (
                (0, 0) if value > 0x4F else (value & 0x0F, (value >> 4) & 0x07)
            )

    def _apply_emergency_command(self, address: int, command_name: str) -> None:
        """Mutate the fake emergency status to mirror a control command."""
        state = self.emergency_info.get(address)
        if state is None:
            return
        status = state["emergency_status"] or 0
        if command_name == "StartFunctionTest":
            status |= 0b00010000
        elif command_name == "StartDurationTest":
            status |= 0b00100000
        elif command_name == "StopTest":
            status &= ~0b00110000
        elif command_name == "Inhibit":
            status |= 0b00000001
        elif command_name == "ReLightResetInhibit":
            status &= ~0b00000001
        state["emergency_status"] = status

    def _gear_present_response(self, address: int) -> SimpleNamespace:
        """Return a QueryControlGearPresent response for a short address.

        A lone gear answers "yes" cleanly, several gear answering at once garble
        the frame into a framing error the driver surfaces, and an empty address
        does not answer at all.
        """
        if address not in self.gears and address not in self.colliding:
            return SimpleNamespace(value=False, raw_value=None)
        error = address in self.colliding
        if address in self.flaky and address not in self._flaky_seen:
            self._flaky_seen.add(address)
            error = True
        return SimpleNamespace(value=True, raw_value=SimpleNamespace(error=error))

    def _serial_at(self, address: int) -> object:
        """Return the identification number a gear reports at a short address."""
        return self.gear_metadata.get(address, self.metadata).get(
            info.IdentificationNumber
        )

    def _set_short_address(self, address: int, dtr0: int | None) -> None:
        """Apply a SetShortAddress written for the gear at a short address."""
        if dtr0 == 255:
            # Clear the address on every gear that answers it; they drop out of
            # the bus until re-commissioned.
            if address in self.gears:
                self._pool.append(
                    _FakeGear(self.gears.pop(address), self._serial_at(address))
                )
                self.levels.pop(address, None)
                self.gear_metadata.pop(address, None)
            self._pool.extend(self.colliding.pop(address, []))
            return
        new = (dtr0 or 0) >> 1
        if address in self.gears:
            self.gears[new] = self.gears.pop(address)
            self.levels[new] = self.levels.pop(address, 0)
            if address in self.gear_metadata:
                self.gear_metadata[new] = self.gear_metadata.pop(address)

    def _commission(self) -> None:
        """Assign free short addresses to unaddressed and collision-freed gear."""
        candidates = (
            range(64)
            if self._commission_available is None
            else self._commission_available
        )
        free = [address for address in candidates if address not in self.gears]
        while self.unaddressed_gears and free:
            address = free.pop(0)
            self.gears[address] = self.unaddressed_gears.pop(0)
            self.levels[address] = 0
        while self._pool and free:
            address = free.pop(0)
            gear = self._pool.pop(0)
            self.gears[address] = gear.device_types
            self.levels[address] = 0
            self.gear_metadata[address] = {
                **self.metadata,
                info.IdentificationNumber: gear.serial,
            }

    def _commission_devices(self) -> None:
        """Assign free short addresses to unaddressed control devices."""
        used = {address for address, _instance in self.dev_inst_map.mapping}
        free = [address for address in range(64) if address not in used]
        while self.unaddressed_devices and free:
            address = free.pop(0)
            for instance, instance_type in self.unaddressed_devices.pop(0):
                self.dev_inst_map.mapping[(address, instance)] = instance_type

    def _close(self) -> None:
        """Close the fake transport."""
        self.closed = True
        self.connected = False


@pytest.fixture
def fake_dali_driver() -> FakeDaliDriver:
    """Return a fake python-dali driver."""
    return FakeDaliDriver("luba232:/dev/ttyACM0")
