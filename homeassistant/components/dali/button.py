"""DALI button entities."""

from typing import override

from dali.address import GearShort
from dali.device.pushbutton import _PushbuttonEvent
from dali.gear.general import IdentifyDevice

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DaliConfigEntry
from .const import DOMAIN
from .coordinator import DaliCoordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DaliConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up pushbutton and identify button entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        DaliPushbutton(coordinator, entry.entry_id, entry.title, address, instance)
        for address, instance, instance_type in coordinator.device_instances
        if instance_type == 1
    )

    known_addresses: set[int] = set()

    def async_add_identify_buttons() -> None:
        new_addresses = set(coordinator.data) - known_addresses
        known_addresses.update(new_addresses)
        async_add_entities(
            DaliIdentify(coordinator, entry.entry_id, entry.title, address)
            for address in new_addresses
        )

    async_add_identify_buttons()
    entry.async_on_unload(coordinator.async_add_listener(async_add_identify_buttons))


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
        self._attr_device_info = coordinator.device_info(
            entry_id, title, address, input_device=True
        )
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


class DaliIdentify(CoordinatorEntity[DaliCoordinator], ButtonEntity):
    """Button that runs the DALI identify procedure on control gear."""

    _attr_has_entity_name = True
    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: DaliCoordinator, entry_id: str, title: str, address: int
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._address = address
        self._attr_unique_id = f"{entry_id}_{address}_identify"
        self._attr_device_info = coordinator.device_info(entry_id, title, address)

    @property
    @override
    def available(self) -> bool:
        """Return whether the DALI gear is responding."""
        return super().available and self._address in self.coordinator.data

    @override
    async def async_press(self) -> None:
        """Run the manufacturer-specific identify procedure for 10 seconds."""
        try:
            await self.coordinator.driver.send(IdentifyDevice(GearShort(self._address)))
        except (OSError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
            ) from err
