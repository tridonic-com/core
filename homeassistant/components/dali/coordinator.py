"""Data update coordinator for the Tridonic DALI integration."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import override

from dali.address import DeviceShort, GearShort, InstanceNumber
from dali.device.general import DTR0, QueryResolution
from dali.device.general_sensor import MeasurementVariable, QueryMeasurementVariable
from dali.driver.serial import DriverLubaRs232
from dali.exceptions import (
    DALISequenceError,
    MemoryLocationNotImplemented,
    ResponseError,
)
from dali.gear.colour import QueryColourTypeFeatures, QueryColourValueDTR
from dali.gear.general import QueryActualLevel, QueryControlGearPresent, QueryMinLevel
from dali.gear.led import QueryDimmingCurve
from dali.gear.sequences import QueryDT8ColourValue
from dali.memory import info
from dali.sequences import QueryDeviceTypes

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

SCAN_INTERVAL = timedelta(seconds=30)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DaliDeviceData:
    """Identification data shared by DALI gears and input devices."""

    gtin: int | None = None
    identification_number: int | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None


@dataclass(frozen=True, slots=True)
class DaliGearData(DaliDeviceData):
    """State and identification data for one DALI control gear."""

    level: int | None = None
    min_level: int = 1
    linear_dimming: bool = False
    device_types: tuple[int, ...] = ()
    supports_color_temperature: bool = False
    supports_xy: bool = False
    color_temp_mired: int | None = None
    color_temp_min_mired: int | None = None
    color_temp_max_mired: int | None = None
    xy_color: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class DaliGeneralSensorData(DaliDeviceData):
    """State and identification data for one Part 306 general sensor."""

    quantity_name: int | None = None
    unit_of_measurement: int | None = None
    resolution: int | None = None
    magnitude: int | None = None
    input_signal_signed: bool | None = None


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
        self._metadata: dict[int, DaliDeviceData] = {}
        self._device_metadata: dict[int, DaliDeviceData] = {}
        self.input_devices: dict[tuple[int, int], DaliGeneralSensorData] = {}
        self._event_listeners: set[Callable[[object], None]] = set()
        self._event_task: asyncio.Task | None = None
        self.event_data: dict[tuple[int, int], object] = {}

    @property
    def device_instances(self) -> tuple[tuple[int, int, int], ...]:
        """Return discovered device instance address/type tuples."""
        return tuple(
            (getattr(address, "address", address), instance, instance_type)
            for (
                address,
                instance,
            ), instance_type in self.driver.dev_inst_map.mapping.items()
        )

    def async_add_event_listener(
        self, listener: Callable[[object], None]
    ) -> Callable[[], None]:
        """Subscribe to DALI-2 input device events."""
        self._event_listeners.add(listener)

        def remove_listener() -> None:
            self._event_listeners.discard(listener)

        return remove_listener

    def start_event_listener(self) -> None:
        """Start consuming DALI-2 events from the driver."""
        self._event_task = asyncio.create_task(self._async_event_listener())

    async def _async_event_listener(self) -> None:
        queue = self.driver.new_dali_rx_queue()
        while True:
            event = await queue.get()
            if not hasattr(event, "short_address"):
                continue
            address = event.short_address
            if address is not None and event.instance_number is not None:
                key = (address.address, event.instance_number)
                self.event_data[key] = event
            for listener in tuple(self._event_listeners):
                listener(event)

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
            supports_color_temperature = False
            supports_xy = False
            color_temp_mired = None
            color_temp_min_mired = None
            color_temp_max_mired = None
            xy_color = None
            if 8 in device_types:
                features = await self.driver.send(QueryColourTypeFeatures(gear_address))
                supports_color_temperature = features.Tc_capable
                supports_xy = features.xy_capable
                if supports_color_temperature:
                    color_temp_mired = await self.driver.run_sequence(
                        QueryDT8ColourValue(
                            gear_address, QueryColourValueDTR.ColourTemperatureTC
                        )
                    )
                    color_temp_min_mired = await self.driver.run_sequence(
                        QueryDT8ColourValue(
                            gear_address,
                            QueryColourValueDTR.ColourTemperatureTcPhysicalCoolest,
                        )
                    )
                    color_temp_max_mired = await self.driver.run_sequence(
                        QueryDT8ColourValue(
                            gear_address,
                            QueryColourValueDTR.ColourTemperatureTcPhysicalWarmest,
                        )
                    )
                if supports_xy:
                    x = await self.driver.run_sequence(
                        QueryDT8ColourValue(
                            gear_address, QueryColourValueDTR.XCoordinate
                        )
                    )
                    y = await self.driver.run_sequence(
                        QueryDT8ColourValue(
                            gear_address, QueryColourValueDTR.YCoordinate
                        )
                    )
                    if isinstance(x, int) and isinstance(y, int):
                        xy_color = (x / 65535, y / 65535)
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
            supports_color_temperature=supports_color_temperature,
            supports_xy=supports_xy,
            color_temp_mired=color_temp_mired,
            color_temp_min_mired=color_temp_min_mired,
            color_temp_max_mired=color_temp_max_mired,
            xy_color=xy_color,
            gtin=metadata.gtin,
            identification_number=metadata.identification_number,
            firmware_version=metadata.firmware_version,
            hardware_version=metadata.hardware_version,
        )

    async def _query_metadata(self, address: GearShort | DeviceShort) -> DaliDeviceData:
        """Read optional identification data from memory bank 0."""
        try:
            values = await self.driver.run_sequence(info.BANK_0.read_all(address))
        except DALISequenceError, MemoryLocationNotImplemented, ResponseError:
            return DaliDeviceData()

        return DaliDeviceData(
            gtin=values.get(info.GTIN),
            identification_number=values.get(info.IdentificationNumber),
            firmware_version=values.get(info.FirmwareVersion),
            hardware_version=values.get(info.HardwareVersion),
        )

    async def _query_general_sensor(
        self, address: DeviceShort, instance: int
    ) -> DaliGeneralSensorData:
        """Return metadata for a Part 306 general sensor instance."""
        instance_address = InstanceNumber(instance)
        metadata = self._device_metadata.get(address.address)
        if metadata is None:
            metadata = await self._query_metadata(address)
            self._device_metadata[address.address] = metadata

        try:
            resolution = _response_value(
                await self.driver.send(QueryResolution(address, instance_address))
            )
            input_signal_signed = _response_value(
                await self._query_measurement_variable(
                    address, instance_address, MeasurementVariable.INPUT_SIGNAL_SIGNED
                )
            )
            unit_of_measurement = _response_value(
                await self._query_measurement_variable(
                    address, instance_address, MeasurementVariable.UNIT_OF_MEASUREMENT
                )
            )
            quantity_name = _response_value(
                await self._query_measurement_variable(
                    address, instance_address, MeasurementVariable.QUANTITY_NAME
                )
            )
            magnitude = _response_value(
                await self._query_measurement_variable(
                    address, instance_address, MeasurementVariable.MAGNITUDE
                )
            )
        except (OSError, TimeoutError) as err:
            raise UpdateFailed("Unable to communicate with the DALI interface") from err

        return DaliGeneralSensorData(
            quantity_name=quantity_name,
            unit_of_measurement=unit_of_measurement,
            resolution=resolution,
            magnitude=magnitude,
            input_signal_signed=bool(input_signal_signed)
            if input_signal_signed is not None
            else None,
            gtin=metadata.gtin,
            identification_number=metadata.identification_number,
            firmware_version=metadata.firmware_version,
            hardware_version=metadata.hardware_version,
        )

    async def _query_measurement_variable(
        self,
        address: DeviceShort,
        instance: InstanceNumber,
        variable: MeasurementVariable,
    ) -> object:
        """Query a Part 306 measurement variable."""
        await self.driver.send(DTR0(variable))
        return await self.driver.send(QueryMeasurementVariable(address, instance))

    @override
    async def _async_update_data(self) -> dict[int, DaliGearData]:
        """Scan all short addresses and query each responding gear."""
        data: dict[int, DaliGearData] = {}
        for address in range(64):
            gear = await self._query_gear(address)
            if gear is not None:
                data[address] = gear
        for address, _instance in self.driver.dev_inst_map.mapping:
            address_int = getattr(address, "address", address)
            if address_int not in self._device_metadata:
                self._device_metadata[address_int] = await self._query_metadata(
                    address
                    if isinstance(address, DeviceShort)
                    else DeviceShort(address)
                )
        for address, instance, instance_type in self.device_instances:
            if instance_type == 6 and (address, instance) not in self.input_devices:
                self.input_devices[
                    (address, instance)
                ] = await self._query_general_sensor(DeviceShort(address), instance)
        return data

    def close(self) -> None:
        """Close the serial transport."""
        if self._event_task:
            self._event_task.cancel()
        if transport := getattr(self.driver, "_transport", None):
            transport.close()

    def device_info(self, entry_id: str, title: str, address: int) -> DeviceInfo:
        """Return shared device information for a DALI gear address."""
        gear = self.data.get(address) or self._device_metadata.get(address)
        if gear is None:
            gear = DaliDeviceData()
        return DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{address}")},
            name=f"{title} gear {address}",
            model=f"GTIN {gear.gtin}" if gear.gtin is not None else "DALI control gear",
            model_id=str(gear.gtin) if gear.gtin is not None else None,
            serial_number=(
                str(gear.identification_number)
                if gear.identification_number is not None
                else None
            ),
            sw_version=gear.firmware_version,
            hw_version=gear.hardware_version,
        )


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


def _response_value(response: object) -> int | None:
    """Return a numeric response value."""
    value = getattr(response, "value", None)
    return value if isinstance(value, int) else None
