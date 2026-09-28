"""DALI-2 pushbutton entities."""

from typing import override

from dali.device.pushbutton import _PushbuttonEvent

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DaliConfigEntry
from .const import DOMAIN
from .coordinator import DaliCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DaliConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up pushbutton entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        DaliPushbutton(coordinator, entry.entry_id, entry.title, address, instance)
        for address, instance, instance_type in coordinator.device_instances
        if instance_type == 1
    )


class DaliPushbutton(ButtonEntity):
    """Representation of a DALI-2 pushbutton instance."""

    _attr_has_entity_name = True
    _attr_translation_key = "pushbutton"

    def __init__(
        self,
        coordinator: DaliCoordinator,
        entry_id: str,
        title: str,
        address: int,
        instance: int,
    ) -> None:
        """Initialize the entity."""
        self._coordinator = coordinator
        self._key = (address, instance)
        self._last_event: str | None = None
        self._attr_unique_id = f"{entry_id}_{address}_{instance}_pushbutton"
        self._attr_device_info = coordinator.device_info(entry_id, title, address)
        coordinator.async_add_event_listener(self._handle_event)

    @property
    @override
    def available(self) -> bool:
        """Return whether the interface is available."""
        return self._coordinator.last_update_success

    @property
    @override
    def extra_state_attributes(self) -> dict[str, str]:
        """Return the last received pushbutton event."""
        return {"last_event": self._last_event} if self._last_event else {}

    @override
    async def async_press(self) -> None:
        """Reject commands because this entity represents an input device."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="pushbutton_not_pressable",
        )

    @callback
    def _handle_event(self, event: object) -> None:
        """Handle a pushbutton event."""
        if (
            isinstance(event, _PushbuttonEvent)
            and (
                event.short_address.address,
                event.instance_number,
            )
            == self._key
        ):
            self._last_event = event.__class__.__name__
            self.async_write_ha_state()
