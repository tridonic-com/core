"""Common fixtures for the Tridonic DALI tests."""

import asyncio
from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dali.command import Command
from dali.device.general import DTR0 as DeviceDTR0, QueryResolution
from dali.device.general_sensor import (
    MeasurementVariable,
    QuantityName,
    QueryMeasurementVariable,
    UnitOfMeasurement,
)
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
    QueryMinLevel,
    QueryMissingShortAddress,
)
from dali.gear.led import QueryDimmingCurve
from dali.memory import info
import pytest


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "homeassistant.components.dali.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


class FakeDaliDriver:
    """Fake python-dali driver for integration tests."""

    def __init__(self, uri: str) -> None:
        """Initialize the fake driver."""
        self.uri = uri
        self.connected = False
        self.closed = False
        self.sent: list[Command] = []
        self.gears = {0: (6,), 1: (1,), 2: (8,)}
        self.levels = {0: 128, 2: 254}
        self.min_levels = {0: 86, 2: 1}
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
        }
        self._transport = SimpleNamespace(close=self._close)
        self.dev_inst_map = SimpleNamespace(mapping={})
        self.event_queue: asyncio.Queue[object] = asyncio.Queue()
        self.measurement_variables = {
            MeasurementVariable.INPUT_SIGNAL_SIGNED: 0,
            MeasurementVariable.UNIT_OF_MEASUREMENT: UnitOfMeasurement.CELSIUS,
            MeasurementVariable.QUANTITY_NAME: QuantityName.TEMPERATURE,
            MeasurementVariable.MAGNITUDE: 127,
        }
        self.resolution = 9
        self._dtr0: int | None = None
        self.unaddressed_gears: list[tuple[int, ...]] = []

    async def connect(self, *, scan_dev_inst: bool = False) -> None:
        """Connect to the fake interface."""
        self.connected = True

    def new_dali_rx_queue(self) -> asyncio.Queue[object]:
        """Return the fake DALI event queue."""
        return self.event_queue

    async def send(self, command: Command) -> SimpleNamespace:
        """Return a response for a fake DALI command."""
        self.sent.append(command)
        if isinstance(command, DeviceDTR0):
            self._dtr0 = command.param
            return SimpleNamespace(value=None)
        if isinstance(command, QueryResolution):
            return SimpleNamespace(value=self.resolution)
        if isinstance(command, QueryMeasurementVariable):
            return SimpleNamespace(value=self.measurement_variables.get(self._dtr0))
        if isinstance(command, QueryMissingShortAddress):
            return SimpleNamespace(value=True if self.unaddressed_gears else None)
        address = command.destination.address
        if isinstance(command, QueryControlGearPresent):
            return SimpleNamespace(value=address in self.gears)
        if isinstance(command, QueryActualLevel):
            return SimpleNamespace(value=self.levels[address])
        if isinstance(command, QueryMinLevel):
            return SimpleNamespace(value=self.min_levels.get(address, 1))
        return SimpleNamespace(value=None)

    async def run_sequence(self, sequence, progress=None):
        """Return a fake sequence response."""
        if sequence.__name__ == "Commissioning":
            sequence.close()
            # Commissioning takes bus time on real hardware.
            await asyncio.sleep(0)
            return self._commission()
        first_command = next(sequence)
        if isinstance(first_command, QueryDeviceType):
            return list(self.gears.get(first_command.destination.address, ()))
        if isinstance(first_command, QueryColourTypeFeatures):
            features = self.color_features.get(first_command.destination.address)
            if features is None:
                return SimpleNamespace(raw_value=None)
            tc_capable, xy_capable = features
            return SimpleNamespace(
                raw_value=SimpleNamespace(error=False),
                Tc_capable=tc_capable,
                xy_capable=xy_capable,
            )
        if isinstance(first_command, QueryDimmingCurve):
            value = (
                1
                if self.linear_dimming.get(first_command.destination.address, False)
                else 0
            )
            return SimpleNamespace(raw_value=SimpleNamespace(as_integer=value))
        if isinstance(first_command, QueryColourStatus):
            tc_active = self.color_tc_active.get(
                first_command.destination.address, False
            )
            return SimpleNamespace(
                raw_value=SimpleNamespace(error=False),
                colour_type_colour_temperature_Tc_active=1 if tc_active else 0,
            )
        if isinstance(first_command, QueryActualLevel):
            dtr_command = next(sequence)
            assert isinstance(dtr_command, DTR0)
            return self.color_values.get(dtr_command.param)
        return self.metadata

    def _commission(self) -> None:
        """Assign free short addresses to unaddressed gear."""
        free = [address for address in range(64) if address not in self.gears]
        while self.unaddressed_gears and free:
            address = free.pop(0)
            self.gears[address] = self.unaddressed_gears.pop(0)
            self.levels[address] = 0

    def _close(self) -> None:
        """Close the fake transport."""
        self.closed = True


@pytest.fixture
def fake_dali_driver() -> FakeDaliDriver:
    """Return a fake python-dali driver."""
    return FakeDaliDriver("luba232:/dev/ttyACM0")
