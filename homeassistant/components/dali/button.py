"""DALI button entities."""

from dataclasses import dataclass
from typing import override

from dali.address import GearShort
from dali.command import Command
from dali.exceptions import DALISequenceError, ResponseError
from dali.gear.emergency import (
    Inhibit,
    ReLightResetInhibit,
    ResetDurationTestDoneFlag,
    ResetFunctionTestDoneFlag,
    ResetLampTime,
    Rest,
    StartDurationTest,
    StartFunctionTest,
    StartIdentification,
    StopTest,
)
from dali.gear.general import IdentifyDevice

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DaliConfigEntry
from .const import DEVICE_TYPE_EMERGENCY, DOMAIN
from .coordinator import DaliCoordinator

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class DaliEmergencyButtonDescription(ButtonEntityDescription):
    """Description of a DT1 emergency command button."""

    command: type[Command]


EMERGENCY_BUTTON_TYPES: tuple[DaliEmergencyButtonDescription, ...] = (
    DaliEmergencyButtonDescription(
        key="start_function_test",
        translation_key="start_function_test",
        command=StartFunctionTest,
    ),
    DaliEmergencyButtonDescription(
        key="start_duration_test",
        translation_key="start_duration_test",
        command=StartDurationTest,
    ),
    DaliEmergencyButtonDescription(
        key="stop_test",
        translation_key="stop_test",
        command=StopTest,
    ),
    DaliEmergencyButtonDescription(
        key="rest",
        translation_key="rest",
        command=Rest,
        entity_category=EntityCategory.CONFIG,
    ),
    DaliEmergencyButtonDescription(
        key="inhibit",
        translation_key="inhibit",
        command=Inhibit,
        entity_category=EntityCategory.CONFIG,
    ),
    DaliEmergencyButtonDescription(
        key="relight_reset_inhibit",
        translation_key="relight_reset_inhibit",
        command=ReLightResetInhibit,
        entity_category=EntityCategory.CONFIG,
    ),
    DaliEmergencyButtonDescription(
        key="reset_lamp_time",
        translation_key="reset_lamp_time",
        command=ResetLampTime,
        entity_category=EntityCategory.CONFIG,
    ),
    DaliEmergencyButtonDescription(
        key="reset_function_test_done_flag",
        translation_key="reset_function_test_done_flag",
        command=ResetFunctionTestDoneFlag,
        entity_category=EntityCategory.CONFIG,
    ),
    DaliEmergencyButtonDescription(
        key="reset_duration_test_done_flag",
        translation_key="reset_duration_test_done_flag",
        command=ResetDurationTestDoneFlag,
        entity_category=EntityCategory.CONFIG,
    ),
    DaliEmergencyButtonDescription(
        key="start_identification",
        translation_key="start_identification",
        command=StartIdentification,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DaliConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up identify and rescan button entities for control gear."""
    coordinator = entry.runtime_data

    async_add_entities([DaliSearchDevices(coordinator, entry.entry_id, entry.title)])

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

    known_emergency: set[int] = set()

    def async_add_emergency_buttons() -> None:
        entities: list[ButtonEntity] = []
        for address, gear in coordinator.data.items():
            if address in known_emergency:
                continue
            if DEVICE_TYPE_EMERGENCY not in gear.device_types:
                continue
            known_emergency.add(address)
            entities.extend(
                DaliEmergencyButton(
                    coordinator, entry.entry_id, entry.title, address, description
                )
                for description in EMERGENCY_BUTTON_TYPES
            )
        async_add_entities(entities)

    async_add_emergency_buttons()
    entry.async_on_unload(coordinator.async_add_listener(async_add_emergency_buttons))


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

    def _identify_command(self) -> Command:
        """Return the identify command for the addressed gear."""
        return IdentifyDevice(GearShort(self._address))

    @override
    async def async_press(self) -> None:
        """Run the identify procedure for 10 seconds."""
        try:
            await self.coordinator.driver.send(self._identify_command())
        except (OSError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
            ) from err


class DaliSearchDevices(CoordinatorEntity[DaliCoordinator], ButtonEntity):
    """Button that rescans the DALI bus for control gear."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "search_devices"

    def __init__(self, coordinator: DaliCoordinator, entry_id: str, title: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_search_devices"
        self._attr_device_info = coordinator.hub_device_info(entry_id, title)

    @override
    async def async_press(self) -> None:
        """Rescan the bus so gear added since the last scan is discovered."""
        await self.coordinator.async_rescan()


class DaliEmergencyButton(CoordinatorEntity[DaliCoordinator], ButtonEntity):
    """Button that issues a DT1 emergency command to control gear."""

    _attr_has_entity_name = True
    entity_description: DaliEmergencyButtonDescription

    def __init__(
        self,
        coordinator: DaliCoordinator,
        entry_id: str,
        title: str,
        address: int,
        description: DaliEmergencyButtonDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._address = address
        self._attr_unique_id = f"{entry_id}_{address}_{description.key}"
        self._attr_device_info = coordinator.device_info(entry_id, title, address)

    @property
    @override
    def available(self) -> bool:
        """Return whether the DALI gear is responding."""
        return super().available and self._address in self.coordinator.data

    @override
    async def async_press(self) -> None:
        """Issue the emergency command, prefixed with ENABLE DEVICE TYPE 1."""
        try:
            await self.coordinator.driver.send(
                self.entity_description.command(GearShort(self._address))
            )
        except (OSError, TimeoutError, DALISequenceError, ResponseError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
            ) from err
        # A test or mode change alters the status bytes, so re-read at once.
        await self.coordinator.async_update_gear(self._address)
