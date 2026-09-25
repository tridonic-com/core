"""Data update coordinator for the Tridonic DALI integration."""

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import override

from dali.address import GearShort
from dali.driver.serial import DriverLubaRs232
from dali.exceptions import (
    DALISequenceError,
    MemoryLocationNotImplemented,
    ResponseError,
)
from dali.gear.general import QueryActualLevel, QueryControlGearPresent, QueryMinLevel
from dali.gear.led import QueryDimmingCurve
from dali.memory import info
from dali.sequences import QueryDeviceTypes

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

SCAN_INTERVAL = timedelta(seconds=30)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DaliGearData:
    """State and identification data for one DALI control gear."""

    level: int | None
    min_level: int = 1
    linear_dimming: bool = False
    device_types: tuple[int, ...] = ()
    gtin: int | None = None
    identification_number: int | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None


class DaliCoordinator(DataUpdateCoordinator[dict[int, DaliGearData]]):
    """Coordinate DALI gear discovery and state updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        driver: DriverLubaRs232,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=SCAN_INTERVAL,
        )
        self.driver = driver
        self._metadata: dict[int, DaliGearData] = {}

    async def _query_gear(self, address: int) -> DaliGearData | None:
        """Return a gear's state and metadata, or None when absent."""
        gear_address = GearShort(address)
        try:
            present = await self.driver.send(QueryControlGearPresent(gear_address))
            if not present.value:
                return None
            device_types = tuple(
                device_type
                for device_type in await self.driver.run_sequence(
                    QueryDeviceTypes(gear_address)
                )
                if device_type in (6, 8)
            )
            if not device_types:
                return None
            level = await self.driver.send(QueryActualLevel(gear_address))
            min_level_response = await self.driver.send(QueryMinLevel(gear_address))
            min_level = min_level_response.value
            if not isinstance(min_level, int) or not 1 <= min_level <= 254:
                min_level = 1
            linear_dimming = False
            if 6 in device_types:
                curve = await self.driver.send(QueryDimmingCurve(gear_address))
                linear_dimming = curve.value.as_integer == 1
        except (OSError, TimeoutError) as err:
            raise UpdateFailed("Unable to communicate with the DALI interface") from err
        except DALISequenceError:
            return None

        metadata = self._metadata.get(address)
        if metadata is None:
            metadata = await self._query_metadata(gear_address)
            self._metadata[address] = metadata

        return DaliGearData(
            level=_dali_level_to_brightness(level.value, linear_dimming, min_level),
            min_level=min_level,
            linear_dimming=linear_dimming,
            device_types=device_types,
            gtin=metadata.gtin,
            identification_number=metadata.identification_number,
            firmware_version=metadata.firmware_version,
            hardware_version=metadata.hardware_version,
        )

    async def _query_metadata(self, address: GearShort) -> DaliGearData:
        """Read optional identification data from memory bank 0."""
        try:
            values = await self.driver.run_sequence(info.BANK_0.read_all(address))
        except DALISequenceError, MemoryLocationNotImplemented, ResponseError:
            return DaliGearData(level=None)

        return DaliGearData(
            level=None,
            gtin=values.get(info.GTIN),
            identification_number=values.get(info.IdentificationNumber),
            firmware_version=values.get(info.FirmwareVersion),
            hardware_version=values.get(info.HardwareVersion),
        )

    @override
    async def _async_update_data(self) -> dict[int, DaliGearData]:
        """Scan all short addresses and query each responding gear."""
        data: dict[int, DaliGearData] = {}
        for address in range(64):
            gear = await self._query_gear(address)
            if gear is not None:
                data[address] = gear
        return data

    def close(self) -> None:
        """Close the serial transport."""
        if transport := getattr(self.driver, "_transport", None):
            transport.close()


def _dali_level_to_brightness(
    level: object, linear_dimming: bool, min_level: int
) -> int | None:
    """Convert a DALI arc-power level to Home Assistant brightness."""
    if not isinstance(level, int) or level >= 255:
        return None
    if level <= 0:
        return 0
    if level <= min_level:
        return 1
    if linear_dimming:
        return round(1 + (level - min_level) * 254 / (254 - min_level))
    return round(255 * 10 ** (((level - 254) * 3) / 253))
