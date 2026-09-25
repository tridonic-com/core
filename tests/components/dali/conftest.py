"""Common fixtures for the Tridonic DALI tests."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dali.command import Command
from dali.gear.colour import QueryColourTypeFeatures, QueryColourValueDTR
from dali.gear.general import (
    DTR0,
    QueryActualLevel,
    QueryControlGearPresent,
    QueryDeviceType,
    QueryMinLevel,
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

    async def connect(self, *, scan_dev_inst: bool = False) -> None:
        """Connect to the fake interface."""
        self.connected = True

    async def send(self, command: Command) -> SimpleNamespace:
        """Return a response for a fake DALI command."""
        self.sent.append(command)
        address = command.destination.address
        if isinstance(command, QueryControlGearPresent):
            return SimpleNamespace(value=address in self.gears)
        if isinstance(command, QueryActualLevel):
            return SimpleNamespace(value=self.levels[address])
        if isinstance(command, QueryMinLevel):
            return SimpleNamespace(value=self.min_levels.get(address, 1))
        if isinstance(command, QueryDimmingCurve):
            value = 1 if self.linear_dimming.get(address, False) else 0
            return SimpleNamespace(value=SimpleNamespace(as_integer=value))
        if isinstance(command, QueryColourTypeFeatures):
            tc_capable, xy_capable = self.color_features.get(address, (False, False))
            return SimpleNamespace(
                Tc_capable=tc_capable,
                xy_capable=xy_capable,
            )
        return SimpleNamespace(value=None)

    async def run_sequence(self, sequence):
        """Return a fake device-type or memory-bank response."""
        first_command = next(sequence)
        if isinstance(first_command, QueryDeviceType):
            return list(self.gears.get(first_command.destination.address, ()))
        if isinstance(first_command, QueryActualLevel):
            dtr_command = next(sequence)
            assert isinstance(dtr_command, DTR0)
            return self.color_values.get(dtr_command.param)
        return self.metadata

    def _close(self) -> None:
        """Close the fake transport."""
        self.closed = True


@pytest.fixture
def fake_dali_driver() -> FakeDaliDriver:
    """Return a fake python-dali driver."""
    return FakeDaliDriver("luba232:/dev/ttyACM0")
