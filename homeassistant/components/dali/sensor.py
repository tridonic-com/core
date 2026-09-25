"""DALI-2 illuminance sensors."""

from typing import override

from dali.device.light import LightEvent

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DaliConfigEntry
from .coordinator import DaliCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DaliConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up illuminance entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        DaliIlluminance(coordinator, entry.entry_id, address, instance)
        for address, instance, instance_type in coordinator.device_instances
        if instance_type == 4
    )


class DaliIlluminance(SensorEntity):
    """Representation of a DALI-2 relative illuminance instance."""

    _attr_has_entity_name = True
    _attr_name = "Illuminance"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: DaliCoordinator, entry_id: str, address: int, instance: int
    ) -> None:
        """Initialize the entity."""
        self._coordinator = coordinator
        self._key = (address, instance)
        self._attr_unique_id = f"{entry_id}_{address}_{instance}_illuminance"
        self._attr_device_info = coordinator.device_info(entry_id, "DALI", address)
        coordinator.async_add_event_listener(self._handle_event)

    @property
    @override
    def available(self) -> bool:
        """Return whether the interface is available."""
        return self._coordinator.last_update_success

    @property
    @override
    def native_value(self) -> int | None:
        """Return the relative illuminance value."""
        event = self._coordinator.event_data.get(self._key)
        return event.illuminance if isinstance(event, LightEvent) else None

    @callback
    def _handle_event(self, event: object) -> None:
        """Handle an illuminance event."""
        if (
            isinstance(event, LightEvent)
            and (
                event.short_address.address,
                event.instance_number,
            )
            == self._key
        ):
            self.async_write_ha_state()
