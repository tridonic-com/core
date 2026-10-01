"""DALI-2 illuminance sensors."""

from dataclasses import dataclass
from typing import override

from dali.device.general_sensor import (
    GeneralSensorEvent,
    QuantityName,
    UnitOfMeasurement,
)
from dali.device.light import LightEvent

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfPressure,
    UnitOfRatio,
    UnitOfSoundPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DaliConfigEntry
from .coordinator import DaliCoordinator, DaliGeneralSensorData

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class DaliPart306SensorDescription(SensorEntityDescription):
    """Description of a Part 306 general sensor."""

    quantity_name: QuantityName


DALI_PART306_SENSOR_TYPES: dict[int, DaliPart306SensorDescription] = {
    QuantityName.TEMPERATURE: DaliPart306SensorDescription(
        key="temperature",
        translation_key="temperature",
        quantity_name=QuantityName.TEMPERATURE,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    QuantityName.RELATIVE_HUMIDITY: DaliPart306SensorDescription(
        key="relative_humidity",
        translation_key="relative_humidity",
        quantity_name=QuantityName.RELATIVE_HUMIDITY,
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    QuantityName.PRESSURE: DaliPart306SensorDescription(
        key="pressure",
        translation_key="pressure",
        quantity_name=QuantityName.PRESSURE,
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PA,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    QuantityName.CO2: DaliPart306SensorDescription(
        key="co2",
        translation_key="co2",
        quantity_name=QuantityName.CO2,
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    QuantityName.VOC: DaliPart306SensorDescription(
        key="voc",
        translation_key="voc",
        quantity_name=QuantityName.VOC,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    QuantityName.AIR_QUALITY_INDEX: DaliPart306SensorDescription(
        key="air_quality_index",
        translation_key="air_quality_index",
        quantity_name=QuantityName.AIR_QUALITY_INDEX,
        device_class=SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    QuantityName.SOUND_PRESSURE_LEVEL: DaliPart306SensorDescription(
        key="sound_pressure",
        translation_key="sound_pressure",
        quantity_name=QuantityName.SOUND_PRESSURE_LEVEL,
        device_class=SensorDeviceClass.SOUND_PRESSURE,
        native_unit_of_measurement=UnitOfSoundPressure.WEIGHTED_DECIBEL_A,
        state_class=SensorStateClass.MEASUREMENT,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DaliConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up illuminance entities."""
    coordinator = entry.runtime_data
    known: set[tuple[int, int]] = set()

    @callback
    def async_add_new_entities() -> None:
        entities: list[SensorEntity] = []
        for address, instance, instance_type in coordinator.device_instances:
            key = (address, instance)
            if key in known:
                continue
            if instance_type == 4:
                entities.append(
                    DaliIlluminance(
                        coordinator, entry.entry_id, entry.title, address, instance
                    )
                )
            elif instance_type == 6 and key in coordinator.input_devices:
                entities.append(
                    DaliPart306Sensor(
                        coordinator, entry.entry_id, entry.title, address, instance
                    )
                )
            else:
                continue
            known.add(key)
        async_add_entities(entities)

    async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_entities))


class DaliIlluminance(SensorEntity):
    """Representation of a DALI-2 relative illuminance instance."""

    _attr_has_entity_name = True
    _attr_translation_key = "illuminance"
    _attr_state_class = SensorStateClass.MEASUREMENT

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
        self._attr_unique_id = f"{entry_id}_{address}_{instance}_illuminance"
        self._attr_device_info = coordinator.device_info(
            entry_id, title, address, input_device=True
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to DALI-2 events once the entity is registered."""
        self.async_on_remove(
            self._coordinator.async_add_event_listener(self._handle_event)
        )

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


class DaliPart306Sensor(SensorEntity):
    """Representation of a DALI-2 Part 306 general sensor instance."""

    _attr_has_entity_name = True

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
        self._data = coordinator.input_devices[self._key]
        description = _description_for_sensor(self._data)
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_unique_id = f"{entry_id}_{address}_{instance}_{description.key}"
        self._attr_device_info = coordinator.device_info(
            entry_id, title, address, input_device=True
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to DALI-2 events once the entity is registered."""
        self.async_on_remove(
            self._coordinator.async_add_event_listener(self._handle_event)
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether the interface is available."""
        return self._coordinator.last_update_success

    @property
    @override
    def native_value(self) -> float | int | None:
        """Return the latest decoded measurement value."""
        event = self._coordinator.event_data.get(self._key)
        if not isinstance(event, GeneralSensorEvent):
            return None
        return _measurement_from_event(event.measured_value, self._data)

    @callback
    def _handle_event(self, event: object) -> None:
        """Handle a Part 306 general sensor event."""
        if (
            isinstance(event, GeneralSensorEvent)
            and event.short_address is not None
            and (
                event.short_address.address,
                event.instance_number,
            )
            == self._key
        ):
            self.async_write_ha_state()


def _description_for_sensor(
    data: DaliGeneralSensorData,
) -> SensorEntityDescription:
    """Return an entity description for a Part 306 sensor."""
    if data.quantity_name in DALI_PART306_SENSOR_TYPES:
        return DALI_PART306_SENSOR_TYPES[data.quantity_name]
    return SensorEntityDescription(
        key=f"part306_{data.quantity_name or 'unknown'}",
        translation_key="part306_sensor",
        native_unit_of_measurement=_unit_for_sensor(data),
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    )


DALI_PART306_UNITS: dict[int, str] = {
    UnitOfMeasurement.CELSIUS: UnitOfTemperature.CELSIUS,
    UnitOfMeasurement.KELVIN: UnitOfTemperature.KELVIN,
    UnitOfMeasurement.PASCAL: UnitOfPressure.PA,
    UnitOfMeasurement.PERCENT: UnitOfRatio.PERCENTAGE,
    UnitOfMeasurement.PARTS_PER_MILLION: UnitOfRatio.PARTS_PER_MILLION,
    UnitOfMeasurement.DECIBEL_A: UnitOfSoundPressure.WEIGHTED_DECIBEL_A,
}


def _unit_for_sensor(data: DaliGeneralSensorData) -> str | None:
    """Return the native unit for an unclassified Part 306 sensor."""
    if data.unit_of_measurement is None:
        return None
    return DALI_PART306_UNITS.get(data.unit_of_measurement)


def _measurement_from_event(
    event_value: int, data: DaliGeneralSensorData
) -> float | int | None:
    """Convert a Part 306 event payload to a measured value."""
    if data.resolution is None or data.magnitude is None:
        return event_value
    if not 1 <= data.resolution <= 24:
        return event_value
    if data.resolution <= 9:
        measured_value = event_value >> (9 - data.resolution)
    else:
        measured_value = event_value << (data.resolution - 9)
    offset = (2 ** (data.resolution - 1)) - 1 if data.input_signal_signed else 0
    value = (measured_value - offset) * (10 ** (data.magnitude - 127))
    return int(value) if value == int(value) else value
