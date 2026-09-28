"""Support for Tridonic DALI lights."""

from collections.abc import Generator
import math
from typing import Any, override

from dali.address import GearShort
from dali.command import Command, Response
from dali.exceptions import DALISequenceError, ResponseError
from dali.gear.colour import (
    Activate,
    SetTemporaryXCoordinate,
    SetTemporaryYCoordinate,
    tc_kelvin_mirek,
)
from dali.gear.general import DAPC, DTR0, DTR1
from dali.gear.sequences import SetDT8ColourValueTc

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DaliConfigEntry
from .coordinator import DaliCoordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DaliConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the DALI light platform."""
    coordinator = entry.runtime_data
    known_addresses: set[int] = set()

    def async_add_new_entities() -> None:
        new_addresses = set(coordinator.data) - known_addresses
        known_addresses.update(new_addresses)
        async_add_entities(
            DaliLight(coordinator, entry.entry_id, entry.title, address)
            for address in new_addresses
        )

    async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_entities))


class DaliLight(CoordinatorEntity[DaliCoordinator], LightEntity):
    """Representation of a DALI light."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(
        self, coordinator: DaliCoordinator, entry_id: str, title: str, address: int
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._address = address
        self._attr_unique_id = f"{entry_id}_{address}"
        self._attr_device_info = coordinator.device_info(entry_id, title, address)
        gear_data = coordinator.data[address]
        color_modes = {ColorMode.BRIGHTNESS}
        if gear_data.supports_color_temperature:
            color_modes.add(ColorMode.COLOR_TEMP)
            self._attr_min_color_temp_kelvin = tc_kelvin_mirek(
                gear_data.color_temp_max_mired or 500
            )
            self._attr_max_color_temp_kelvin = tc_kelvin_mirek(
                gear_data.color_temp_min_mired or 153
            )
        if gear_data.supports_xy:
            color_modes.add(ColorMode.XY)
        if len(color_modes) > 1:
            color_modes.discard(ColorMode.BRIGHTNESS)
        self._attr_supported_color_modes = color_modes

    @property
    @override
    def available(self) -> bool:
        """Return whether the DALI gear is responding."""
        return super().available and self._address in self.coordinator.data

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the DALI gear is on."""
        return bool(self.coordinator.data[self._address].level)

    @property
    @override
    def brightness(self) -> int:
        """Return the current DALI arc power level."""
        return self.coordinator.data[self._address].level or 0

    @property
    @override
    def color_mode(self) -> ColorMode:
        """Return the currently reported color mode."""
        gear_data = self.coordinator.data[self._address]
        if gear_data.supports_xy and gear_data.xy_color is not None:
            return ColorMode.XY
        if gear_data.supports_color_temperature and gear_data.color_temp_mired:
            return ColorMode.COLOR_TEMP
        return ColorMode.BRIGHTNESS

    @property
    @override
    def color_temp_kelvin(self) -> int | None:
        """Return the current color temperature."""
        value = self.coordinator.data[self._address].color_temp_mired
        return tc_kelvin_mirek(value) if value else None

    @property
    @override
    def xy_color(self) -> tuple[float, float] | None:
        """Return the current CIE xy color."""
        return self.coordinator.data[self._address].xy_color

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        try:
            brightness = max(1, min(kwargs.get("brightness", 255), 255))
            gear_data = self.coordinator.data[self._address]
            if color_temp := kwargs.get("color_temp_kelvin"):
                await self.coordinator.driver.run_sequence(
                    SetDT8ColourValueTc(self._address, tc_kelvin_mirek(color_temp))
                )
            if xy_color := kwargs.get("xy_color"):
                await self.coordinator.driver.run_sequence(
                    _set_xy_color(self._address, xy_color)
                )
            if gear_data.linear_dimming:
                level = round(
                    gear_data.min_level
                    + (brightness - 1) * (254 - gear_data.min_level) / 254
                )
            else:
                level = round(
                    254 + 253 * math.log10(brightness / 255) / 3
                    if brightness < 255
                    else 254
                )
            await self.coordinator.driver.send(
                DAPC(
                    GearShort(self._address),
                    max(gear_data.min_level, min(level, 254)),
                )
            )
            await self.coordinator.async_request_refresh()
        except (OSError, TimeoutError, DALISequenceError, ResponseError) as err:
            raise HomeAssistantError(
                "Unable to communicate with the DALI gear"
            ) from err

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        try:
            await self.coordinator.driver.send(DAPC(GearShort(self._address), 0))
            await self.coordinator.async_request_refresh()
        except (OSError, TimeoutError, DALISequenceError, ResponseError) as err:
            raise HomeAssistantError(
                "Unable to communicate with the DALI gear"
            ) from err


def _set_xy_color(
    address: GearShort, xy_color: tuple[float, float]
) -> Generator[Command, Response | None]:
    """Create the DT8 sequence for setting an xy color."""
    for value, command in zip(
        xy_color, (SetTemporaryXCoordinate, SetTemporaryYCoordinate), strict=True
    ):
        raw_value = round(max(0, min(value, 1)) * 65535).to_bytes(2, "little")
        yield DTR0(raw_value[0])
        yield DTR1(raw_value[1])
        yield command(address)
    yield Activate(address)
