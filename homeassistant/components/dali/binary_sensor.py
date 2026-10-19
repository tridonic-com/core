"""DALI control-gear diagnostic binary sensors."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DaliConfigEntry
from .coordinator import DaliCoordinator, DaliDiagnosticsData

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class DaliGearDiagnosticBinarySensorDescription(BinarySensorEntityDescription):
    """Description of a memory bank 205 or 206 failure condition."""

    value_fn: Callable[[DaliDiagnosticsData], bool | None]


GEAR_DIAGNOSTIC_BINARY_SENSOR_TYPES: tuple[
    DaliGearDiagnosticBinarySensorDescription, ...
] = (
    DaliGearDiagnosticBinarySensorDescription(
        key="control_gear_overall_failure",
        translation_key="control_gear_overall_failure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_overall_failure,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="control_gear_undervoltage",
        translation_key="control_gear_undervoltage",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_undervoltage,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="control_gear_overvoltage",
        translation_key="control_gear_overvoltage",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_overvoltage,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="control_gear_output_power_limitation",
        translation_key="control_gear_output_power_limitation",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_output_power_limitation,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="control_gear_thermal_derating",
        translation_key="control_gear_thermal_derating",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_thermal_derating,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="control_gear_thermal_shutdown",
        translation_key="control_gear_thermal_shutdown",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_thermal_shutdown,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="light_source_overall_failure",
        translation_key="light_source_overall_failure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_overall_failure,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="light_source_short_circuit",
        translation_key="light_source_short_circuit",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_short_circuit,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="light_source_open_circuit",
        translation_key="light_source_open_circuit",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_open_circuit,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="light_source_thermal_derating",
        translation_key="light_source_thermal_derating",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_thermal_derating,
    ),
    DaliGearDiagnosticBinarySensorDescription(
        key="light_source_thermal_shutdown",
        translation_key="light_source_thermal_shutdown",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_thermal_shutdown,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DaliConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the DALI control-gear diagnostic binary sensors."""
    coordinator = entry.runtime_data

    known_diagnostics: set[int] = set()

    @callback
    def async_add_gear_diagnostic_binary_sensors() -> None:
        entities: list[BinarySensorEntity] = []
        for address, gear in coordinator.data.items():
            if gear.diagnostics is None or address in known_diagnostics:
                continue
            known_diagnostics.add(address)
            entities.extend(
                DaliGearDiagnosticBinarySensor(
                    coordinator, entry.entry_id, entry.title, address, description
                )
                for description in GEAR_DIAGNOSTIC_BINARY_SENSOR_TYPES
            )
        async_add_entities(entities)

    async_add_gear_diagnostic_binary_sensors()
    entry.async_on_unload(
        coordinator.async_add_listener(async_add_gear_diagnostic_binary_sensors)
    )


class DaliGearDiagnosticBinarySensor(
    CoordinatorEntity[DaliCoordinator], BinarySensorEntity
):
    """Representation of one memory bank 205 or 206 failure condition.

    The gear latches each condition until it clears, so these report what the
    gear last observed rather than what the last read happened to catch.
    """

    _attr_has_entity_name = True
    entity_description: DaliGearDiagnosticBinarySensorDescription

    def __init__(
        self,
        coordinator: DaliCoordinator,
        entry_id: str,
        title: str,
        address: int,
        description: DaliGearDiagnosticBinarySensorDescription,
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
        """Return whether the diagnostics memory banks have been read."""
        gear = self.coordinator.data.get(self._address)
        return super().available and gear is not None and gear.diagnostics is not None

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether the described failure condition is present."""
        gear = self.coordinator.data.get(self._address)
        if gear is None or gear.diagnostics is None:
            return None
        return self.entity_description.value_fn(gear.diagnostics)
