"""Support for Tridonic DALI lights."""

import math
from typing import Any, override

from dali.address import GearShort
from dali.gear.general import DAPC

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DaliConfigEntry
from .const import DOMAIN
from .coordinator import DaliCoordinator


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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=f"{title} gear {address}",
            model=(
                f"GTIN {coordinator.data[address].gtin}"
                if coordinator.data[address].gtin is not None
                else "DALI control gear"
            ),
            model_id=(
                str(coordinator.data[address].gtin)
                if coordinator.data[address].gtin is not None
                else None
            ),
            serial_number=(
                str(coordinator.data[address].identification_number)
                if coordinator.data[address].identification_number is not None
                else None
            ),
            sw_version=coordinator.data[address].firmware_version,
            hw_version=coordinator.data[address].hardware_version,
        )

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

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        brightness = max(1, min(kwargs.get("brightness", 255), 255))
        gear_data = self.coordinator.data[self._address]
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

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.coordinator.driver.send(DAPC(GearShort(self._address), 0))
        await self.coordinator.async_request_refresh()
