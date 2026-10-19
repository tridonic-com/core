"""DALI identification and luminaire sensors."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DaliConfigEntry
from .coordinator import (
    DaliCoordinator,
    DaliDeviceData,
    DaliDiagnosticsData,
    DaliGearData,
    DaliLuminaireData,
    DaliMaintenanceData,
)

PARALLEL_UPDATES = 0

# Luminous flux, which Home Assistant has no unit constant for.
UNIT_LUMEN = "lm"


@dataclass(frozen=True, kw_only=True)
class DaliIdentificationSensorDescription(SensorEntityDescription):
    """Description of a memory bank 0 identification value."""

    value_fn: Callable[[DaliDeviceData], str | int | None]


IDENTIFICATION_SENSOR_TYPES: tuple[DaliIdentificationSensorDescription, ...] = (
    DaliIdentificationSensorDescription(
        key="identification_number",
        translation_key="identification_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.identification_number,
    ),
)

# The decoded light distribution types of IEC 62386-102 section 9.10.7, keyed
# by the display string python-dali turns the raw value into.
LIGHT_DISTRIBUTION_TYPES = {
    "not specified": "not_specified",
    "Type I": "type_i",
    "Type II": "type_ii",
    "Type III": "type_iii",
    "Type IV": "type_iv",
    "Type V": "type_v",
    "reserved": "reserved",
}


def _light_distribution_type(data: DaliLuminaireData) -> str | None:
    """Return the light distribution type as a translatable option."""
    if data.light_distribution_type is None:
        return None
    return LIGHT_DISTRIBUTION_TYPES.get(data.light_distribution_type)


@dataclass(frozen=True, kw_only=True)
class DaliLuminaireSensorDescription(SensorEntityDescription):
    """Description of a memory bank 1 luminaire value."""

    value_fn: Callable[[DaliLuminaireData], int | str | None]


LUMINAIRE_SENSOR_TYPES: tuple[DaliLuminaireSensorDescription, ...] = (
    DaliLuminaireSensorDescription(
        key="manufacturer_gtin",
        translation_key="manufacturer_gtin",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.manufacturer_gtin,
    ),
    DaliLuminaireSensorDescription(
        key="luminaire_id",
        translation_key="luminaire_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.luminaire_id,
    ),
    DaliLuminaireSensorDescription(
        key="luminaire_identification",
        translation_key="luminaire_identification",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.luminaire_identification or None,
    ),
    DaliLuminaireSensorDescription(
        key="luminaire_color",
        translation_key="luminaire_color",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.luminaire_color or None,
    ),
    DaliLuminaireSensorDescription(
        key="year_of_manufacture",
        translation_key="year_of_manufacture",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.year_of_manufacture,
    ),
    DaliLuminaireSensorDescription(
        key="week_of_manufacture",
        translation_key="week_of_manufacture",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.week_of_manufacture,
    ),
    DaliLuminaireSensorDescription(
        key="input_power_nominal",
        translation_key="input_power_nominal",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.input_power_nominal,
    ),
    DaliLuminaireSensorDescription(
        key="input_power_minimum_dim",
        translation_key="input_power_minimum_dim",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.input_power_minimum_dim,
    ),
    DaliLuminaireSensorDescription(
        key="mains_voltage_minimum",
        translation_key="mains_voltage_minimum",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.mains_voltage_minimum,
    ),
    DaliLuminaireSensorDescription(
        key="mains_voltage_maximum",
        translation_key="mains_voltage_maximum",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.mains_voltage_maximum,
    ),
    DaliLuminaireSensorDescription(
        key="light_output_nominal",
        translation_key="light_output_nominal",
        native_unit_of_measurement=UNIT_LUMEN,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_output_nominal,
    ),
    DaliLuminaireSensorDescription(
        key="cri",
        translation_key="cri",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.cri,
    ),
    DaliLuminaireSensorDescription(
        key="cct",
        translation_key="cct",
        # Kelvin without a device class: this is the nominal correlated color
        # temperature of the light source, not a temperature reading, so it
        # must not be converted into the user's temperature unit.
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.cct,
    ),
    DaliLuminaireSensorDescription(
        key="light_distribution_type",
        translation_key="light_distribution_type",
        device_class=SensorDeviceClass.ENUM,
        options=list(LIGHT_DISTRIBUTION_TYPES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_light_distribution_type,
    ),
)


@dataclass(frozen=True, kw_only=True)
class DaliMaintenanceSensorDescription(SensorEntityDescription):
    """Description of a memory bank 207 rated-life value."""

    value_fn: Callable[[DaliMaintenanceData], int | None]


MAINTENANCE_SENSOR_TYPES: tuple[DaliMaintenanceSensorDescription, ...] = (
    DaliMaintenanceSensorDescription(
        key="rated_median_useful_life",
        translation_key="rated_median_useful_life",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.rated_median_useful_life,
    ),
    DaliMaintenanceSensorDescription(
        key="rated_median_useful_light_source_starts",
        translation_key="rated_median_useful_light_source_starts",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.rated_median_useful_light_source_starts,
    ),
    DaliMaintenanceSensorDescription(
        key="control_gear_reference_temperature",
        translation_key="control_gear_reference_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_reference_temperature,
    ),
)


@dataclass(frozen=True, kw_only=True)
class DaliGearDiagnosticSensorDescription(SensorEntityDescription):
    """Description of a memory bank 205 or 206 diagnostics measurement."""

    value_fn: Callable[[DaliDiagnosticsData], int | float | None]


GEAR_DIAGNOSTIC_SENSOR_TYPES: tuple[DaliGearDiagnosticSensorDescription, ...] = (
    DaliGearDiagnosticSensorDescription(
        key="control_gear_operating_time",
        translation_key="control_gear_operating_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_operating_time,
    ),
    DaliGearDiagnosticSensorDescription(
        key="control_gear_start_counter",
        translation_key="control_gear_start_counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_start_counter,
    ),
    DaliGearDiagnosticSensorDescription(
        key="control_gear_external_supply_voltage",
        translation_key="control_gear_external_supply_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_external_supply_voltage,
    ),
    DaliGearDiagnosticSensorDescription(
        key="control_gear_supply_frequency",
        translation_key="control_gear_supply_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_supply_frequency,
    ),
    DaliGearDiagnosticSensorDescription(
        key="control_gear_power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_power_factor,
    ),
    DaliGearDiagnosticSensorDescription(
        key="control_gear_internal_temperature",
        translation_key="control_gear_internal_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_internal_temperature,
    ),
    DaliGearDiagnosticSensorDescription(
        key="control_gear_output_current_percent",
        translation_key="control_gear_output_current_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.control_gear_output_current_percent,
    ),
    DaliGearDiagnosticSensorDescription(
        key="light_source_on_time",
        translation_key="light_source_on_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_on_time,
    ),
    DaliGearDiagnosticSensorDescription(
        key="light_source_on_time_resettable",
        translation_key="light_source_on_time_resettable",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_on_time_resettable,
    ),
    DaliGearDiagnosticSensorDescription(
        key="light_source_start_counter",
        translation_key="light_source_start_counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_start_counter,
    ),
    DaliGearDiagnosticSensorDescription(
        key="light_source_start_counter_resettable",
        translation_key="light_source_start_counter_resettable",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_start_counter_resettable,
    ),
    DaliGearDiagnosticSensorDescription(
        key="light_source_voltage",
        translation_key="light_source_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_voltage,
    ),
    DaliGearDiagnosticSensorDescription(
        key="light_source_current",
        translation_key="light_source_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_current,
    ),
    DaliGearDiagnosticSensorDescription(
        key="light_source_temperature",
        translation_key="light_source_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.light_source_temperature,
    ),
)

ACTIVE_ENERGY_SENSOR = SensorEntityDescription(
    key="active_energy",
    translation_key="active_energy",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    state_class=SensorStateClass.TOTAL_INCREASING,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DaliConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the DALI identification and luminaire sensors."""
    coordinator = entry.runtime_data

    known_identification_gear: set[int] = set()

    @callback
    def async_add_identification_sensors() -> None:
        entities: list[SensorEntity] = []
        for address in set(coordinator.data) - known_identification_gear:
            known_identification_gear.add(address)
            entities.append(
                DaliAddressSensor(coordinator, entry.entry_id, entry.title, address)
            )
            entities.extend(
                DaliIdentificationSensor(
                    coordinator, entry.entry_id, entry.title, address, description
                )
                for description in IDENTIFICATION_SENSOR_TYPES
            )
        async_add_entities(entities)

    async_add_identification_sensors()
    entry.async_on_unload(
        coordinator.async_add_listener(async_add_identification_sensors)
    )

    known_luminaire: set[int] = set()
    known_maintenance: set[int] = set()
    known_diagnostics: set[int] = set()
    known_energy: set[int] = set()

    @callback
    def async_add_memory_bank_sensors() -> None:
        entities: list[SensorEntity] = []
        for address, gear in coordinator.data.items():
            if gear.luminaire is not None and address not in known_luminaire:
                known_luminaire.add(address)
                entities.extend(
                    DaliLuminaireSensor(
                        coordinator, entry.entry_id, entry.title, address, description
                    )
                    for description in LUMINAIRE_SENSOR_TYPES
                )
            if gear.maintenance is not None and address not in known_maintenance:
                known_maintenance.add(address)
                entities.extend(
                    DaliMaintenanceSensor(
                        coordinator, entry.entry_id, entry.title, address, description
                    )
                    for description in MAINTENANCE_SENSOR_TYPES
                )
            if gear.diagnostics is not None and address not in known_diagnostics:
                known_diagnostics.add(address)
                entities.extend(
                    DaliGearDiagnosticSensor(
                        coordinator, entry.entry_id, entry.title, address, description
                    )
                    for description in GEAR_DIAGNOSTIC_SENSOR_TYPES
                )
            if gear.active_energy is not None and address not in known_energy:
                known_energy.add(address)
                entities.append(
                    DaliEnergySensor(
                        coordinator,
                        entry.entry_id,
                        entry.title,
                        address,
                        ACTIVE_ENERGY_SENSOR,
                    )
                )
        async_add_entities(entities)

    async_add_memory_bank_sensors()
    entry.async_on_unload(coordinator.async_add_listener(async_add_memory_bank_sensors))


class DaliIdentificationSensor(CoordinatorEntity[DaliCoordinator], SensorEntity):
    """Representation of a memory bank 0 identification value of a bus unit.

    The device registry shows the batch and serial number joined in its single
    serial number field, so this diagnostic entity is what exposes the undivided
    value memory bank 0 actually stores.
    """

    _attr_has_entity_name = True
    entity_description: DaliIdentificationSensorDescription

    def __init__(
        self,
        coordinator: DaliCoordinator,
        entry_id: str,
        title: str,
        address: int,
        description: DaliIdentificationSensorDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._address = address
        self._attr_unique_id = f"{entry_id}_{address}_{description.key}"
        self._attr_device_info = coordinator.device_info(entry_id, title, address)

    @property
    @override
    def native_value(self) -> str | int | None:
        """Return the identification value read from memory bank 0."""
        return self.entity_description.value_fn(
            self.coordinator.device_data(self._address)
        )


class DaliAddressSensor(CoordinatorEntity[DaliCoordinator], SensorEntity):
    """Representation of a bus unit's DALI short address.

    The device registry has no field for the bus address, so this diagnostic
    entity exposes it. The address is also part of the device name.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "dali_address"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: DaliCoordinator,
        entry_id: str,
        title: str,
        address: int,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._address = address
        prefix = f"{entry_id}_{address}_"
        self._attr_unique_id = f"{prefix}dali_address"
        self._attr_device_info = coordinator.device_info(entry_id, title, address)

    @property
    @override
    def native_value(self) -> int:
        """Return the bus short address."""
        return self._address


class DaliGearBankSensor(CoordinatorEntity[DaliCoordinator], SensorEntity):
    """Base for a sensor reading one value out of an extended memory bank.

    A bus unit implements each bank independently, so an entity reports itself
    unavailable until its own bank has answered.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DaliCoordinator,
        entry_id: str,
        title: str,
        address: int,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._address = address
        self._attr_unique_id = f"{entry_id}_{address}_{description.key}"
        self._attr_device_info = coordinator.device_info(entry_id, title, address)

    @property
    def _gear(self) -> DaliGearData | None:
        """Return the data of the control gear this sensor belongs to."""
        return self.coordinator.data.get(self._address)


class DaliLuminaireSensor(DaliGearBankSensor):
    """Representation of a memory bank 1 luminaire value of a control gear."""

    entity_description: DaliLuminaireSensorDescription

    @property
    @override
    def available(self) -> bool:
        """Return whether the luminaire memory bank has been read."""
        gear = self._gear
        return super().available and gear is not None and gear.luminaire is not None

    @property
    @override
    def native_value(self) -> int | str | None:
        """Return the described luminaire value."""
        gear = self._gear
        if gear is None or gear.luminaire is None:
            return None
        return self.entity_description.value_fn(gear.luminaire)


class DaliMaintenanceSensor(DaliGearBankSensor):
    """Representation of a memory bank 207 rated-life value of a control gear."""

    entity_description: DaliMaintenanceSensorDescription

    @property
    @override
    def available(self) -> bool:
        """Return whether the maintenance memory bank has been read."""
        gear = self._gear
        return super().available and gear is not None and gear.maintenance is not None

    @property
    @override
    def native_value(self) -> int | None:
        """Return the described rated-life value."""
        gear = self._gear
        if gear is None or gear.maintenance is None:
            return None
        return self.entity_description.value_fn(gear.maintenance)


class DaliGearDiagnosticSensor(DaliGearBankSensor):
    """Representation of a memory bank 205 or 206 diagnostics measurement."""

    entity_description: DaliGearDiagnosticSensorDescription

    @property
    @override
    def available(self) -> bool:
        """Return whether the diagnostics memory banks have been read."""
        gear = self._gear
        return super().available and gear is not None and gear.diagnostics is not None

    @property
    @override
    def native_value(self) -> int | float | None:
        """Return the described diagnostics measurement."""
        gear = self._gear
        if gear is None or gear.diagnostics is None:
            return None
        return self.entity_description.value_fn(gear.diagnostics)


class DaliEnergySensor(DaliGearBankSensor):
    """Representation of the memory bank 202 active energy total of a gear."""

    @property
    @override
    def available(self) -> bool:
        """Return whether the energy memory bank has been read."""
        gear = self._gear
        return super().available and gear is not None and gear.active_energy is not None

    @property
    @override
    def native_value(self) -> float | None:
        """Return the energy the gear has consumed since it was built."""
        gear = self._gear
        return gear.active_energy if gear is not None else None
