"""Data update coordinator for the Tridonic DALI integration."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
import logging
import math
from typing import override

from dali.address import DeviceShort, GearBroadcast, GearShort, InstanceNumber
from dali.device.general import DTR0, QueryResolution
from dali.device.general_sensor import MeasurementVariable, QueryMeasurementVariable
from dali.driver.serial import DriverLubaRs232
from dali.exceptions import (
    DALISequenceError,
    MemoryLocationNotImplemented,
    ResponseError,
)
from dali.gear.colour import QueryColourValueDTR
from dali.gear.general import (
    QueryActualLevel,
    QueryControlGearPresent,
    QueryMinLevel,
    QueryMissingShortAddress,
)
from dali.gear.sequences import (
    QueryDT6DimmingCurve,
    QueryDT8ColourStatus,
    QueryDT8ColourTypeFeatures,
    QueryDT8ColourValue,
)
from dali.memory import info
from dali.sequences import Commissioning, QueryDeviceTypes, progress

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, ISSUE_UNADDRESSED_GEAR, unaddressed_gear_issue_id

SCAN_INTERVAL = timedelta(seconds=30)
FULL_SCAN_INTERVAL = timedelta(minutes=5)
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
    color_type_tc_active: bool | None = None
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

    config_entry: ConfigEntry

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
        self._last_full_scan: float | None = None
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
                # A faulty listener must not kill the shared event task and
                # silently stop delivery to every other entity.
                try:
                    listener(event)
                except Exception:
                    LOGGER.exception("Unhandled error in DALI event listener")

    async def async_update_gear(
        self, address: int, optimistic_level: int | None = None
    ) -> None:
        """Refresh a single gear and notify entities, without a full bus scan.

        ``optimistic_level`` overrides the queried brightness with the value a
        just-sent level settles to. A DALI gear reaches a commanded level over
        its configured fade time, so an immediate ``QueryActualLevel`` reads a
        mid-fade value; the override shows the target at once and the periodic
        scan reconciles it.
        """
        gear = await self._query_gear(address)
        data = dict(self.data or {})
        if gear is None:
            data.pop(address, None)
        elif optimistic_level is not None:
            data[address] = replace(gear, level=optimistic_level)
        else:
            data[address] = gear
        self.async_set_updated_data(data)

    async def _query_gear(self, address: int) -> DaliGearData | None:
        """Return a gear's state and metadata, or None when absent.

        A known gear is queried again after a miss so a single dropped DALI
        response does not flip a light to unavailable.
        """
        gear = await self._query_gear_once(address)
        if gear is None and self.data and address in self.data:
            gear = await self._query_gear_once(address)
        return gear

    async def _query_gear_once(self, address: int) -> DaliGearData | None:
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
                curve = await self.driver.run_sequence(
                    QueryDT6DimmingCurve(gear_address)
                )
                linear_dimming = (
                    curve is not None
                    and curve.raw_value is not None
                    and curve.raw_value.as_integer == 1
                )
            supports_color_temperature = False
            supports_xy = False
            color_type_tc_active = None
            color_temp_mired = None
            color_temp_min_mired = None
            color_temp_max_mired = None
            xy_color = None
            if 8 in device_types:
                features = await self.driver.run_sequence(
                    QueryDT8ColourTypeFeatures(gear_address)
                )
                if features.raw_value is not None and not features.raw_value.error:
                    supports_color_temperature = features.Tc_capable
                    supports_xy = features.xy_capable
                if supports_color_temperature and supports_xy:
                    status = await self.driver.run_sequence(
                        QueryDT8ColourStatus(gear_address)
                    )
                    if status.raw_value is not None and not status.raw_value.error:
                        color_type_tc_active = (
                            status.colour_type_colour_temperature_Tc_active == 1
                        )
                if supports_color_temperature:
                    color_temp_mired = await self.driver.run_sequence(
                        QueryDT8ColourValue(
                            gear_address, QueryColourValueDTR.ColourTemperatureTC
                        )
                    )
                    # The gear only accepts values within its configured Tc
                    # limits; fall back to the physical limits when unset.
                    color_temp_min_mired = await self.driver.run_sequence(
                        QueryDT8ColourValue(
                            gear_address,
                            QueryColourValueDTR.ColourTemperatureTcCoolest,
                        )
                    )
                    if color_temp_min_mired is None:
                        color_temp_min_mired = await self.driver.run_sequence(
                            QueryDT8ColourValue(
                                gear_address,
                                QueryColourValueDTR.ColourTemperatureTcPhysicalCoolest,
                            )
                        )
                    color_temp_max_mired = await self.driver.run_sequence(
                        QueryDT8ColourValue(
                            gear_address,
                            QueryColourValueDTR.ColourTemperatureTcWarmest,
                        )
                    )
                    if color_temp_max_mired is None:
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
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
            ) from err
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
            color_type_tc_active=color_type_tc_active,
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
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
            ) from err

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
        """Refresh known gear, scanning the whole bus periodically.

        Every cycle re-queries the gear already known so lights track external
        changes, but the full 64-address discovery scan (and the associated
        device-instance and unaddressed-gear checks) only runs every
        ``FULL_SCAN_INTERVAL`` to keep bus traffic low.
        """
        now = self.hass.loop.time()
        full_scan = (
            self._last_full_scan is None
            or now - self._last_full_scan >= FULL_SCAN_INTERVAL.total_seconds()
        )
        addresses = range(64) if full_scan else tuple(self.data or ())
        data: dict[int, DaliGearData] = {}
        for address in addresses:
            gear = await self._query_gear(address)
            if gear is not None:
                data[address] = gear
        if not full_scan:
            return data
        self._last_full_scan = now
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
        self._update_unaddressed_gear_issue(await self._has_unaddressed_gear())
        return data

    async def _has_unaddressed_gear(self) -> bool:
        """Return whether any control gear on the bus lacks a short address."""
        try:
            response = await self.driver.send(QueryMissingShortAddress(GearBroadcast()))
        except (OSError, TimeoutError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
            ) from err
        return bool(response.value)

    def _update_unaddressed_gear_issue(self, unaddressed: bool) -> None:
        """Create or remove the repair issue for unaddressed control gear."""
        issue_id = unaddressed_gear_issue_id(self.config_entry.entry_id)
        if not unaddressed:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            data={"entry_id": self.config_entry.entry_id},
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_UNADDRESSED_GEAR,
            translation_placeholders={"title": self.config_entry.title},
        )

    async def async_assign_short_addresses(self) -> bool:
        """Address unaddressed control gear and return whether any remain unaddressed."""
        await self.driver.run_sequence(
            Commissioning(readdress=False), progress=_log_commissioning_progress
        )
        unaddressed = await self._has_unaddressed_gear()
        # Force a full bus scan so newly addressed gear is discovered at once.
        self._last_full_scan = None
        await self.async_refresh()
        return unaddressed

    def close(self) -> None:
        """Close the serial transport."""
        if self._event_task:
            self._event_task.cancel()
        if transport := getattr(self.driver, "_transport", None):
            transport.close()

    def device_info(
        self, entry_id: str, title: str, address: int, *, input_device: bool = False
    ) -> DeviceInfo:
        """Return shared device information for a DALI gear or input device.

        DALI control gear (lights) and control devices (sensors, buttons) share
        the same numeric address space but are distinct on the bus, so the name
        distinguishes them and the model default reflects the correct kind.
        """
        if input_device:
            data = self._device_metadata.get(address) or self.data.get(address)
        else:
            data = self.data.get(address) or self._device_metadata.get(address)
        if data is None:
            data = DaliDeviceData()
        kind = "sensor" if input_device else "gear"
        default_model = "DALI control device" if input_device else "DALI control gear"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{address}")},
            name=f"{self._interface_label(title)}/{address} ({kind})",
            model=f"GTIN {data.gtin}" if data.gtin is not None else default_model,
            model_id=str(data.gtin) if data.gtin is not None else None,
            serial_number=(
                str(data.identification_number)
                if data.identification_number is not None
                else None
            ),
            sw_version=data.firmware_version,
            hw_version=data.hardware_version,
        )

    def _interface_label(self, title: str) -> str:
        """Return a short interface label from the entry's USB identity.

        The config-entry title is the full human-readable serial-port string,
        which is too long for a device name. The USB unique ID is a structured
        ``vid:pid_serial_manufacturer_description`` field, so a compact
        ``model_serial`` label is derived from it, falling back to the title
        for non-USB interfaces that have no unique ID.
        """
        unique_id = self.config_entry.unique_id
        if unique_id:
            parts = unique_id.split("_", 3)
            if len(parts) == 4:
                _vid_pid, serial, _manufacturer, description = parts
                if serial:
                    model = description.replace(" ", "_")
                    return f"{model}_{serial}" if model else serial
        return title


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


def _brightness_to_dali_level(
    brightness: int, linear_dimming: bool, min_level: int
) -> int:
    """Convert Home Assistant brightness (1-255) to a DALI arc-power level.

    The DALI level axis is coarser than the 1-255 brightness axis, so rounding
    each direction independently makes a set level read back as a different
    brightness and the UI slider jumps. The level is therefore snapped to the
    one that ``_dali_level_to_brightness`` maps back closest to the requested
    brightness, keeping set-then-read stable wherever a matching level exists.
    """
    if linear_dimming:
        analytic = min_level + (brightness - 1) * (254 - min_level) / 254
    elif brightness < 255:
        analytic = 254 + 253 * math.log10(brightness / 255) / 3
    else:
        analytic = 254
    base = max(min_level, min(round(analytic), 254))
    candidates = range(max(min_level, base - 2), min(254, base + 2) + 1)
    return min(
        candidates,
        key=lambda level: (
            abs(
                (_dali_level_to_brightness(level, linear_dimming, min_level) or 0)
                - brightness
            ),
            abs(level - base),
        ),
    )


def _response_value(response: object) -> int | None:
    """Return a numeric response value."""
    value = getattr(response, "value", None)
    return value if isinstance(value, int) else None


def _log_commissioning_progress(update: str | progress) -> None:
    """Log progress messages from the commissioning sequence."""
    message = update if isinstance(update, str) else update.message
    if message:
        LOGGER.debug("Commissioning: %s", message)
