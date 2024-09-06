from dataclasses import dataclass
from typing import Awaitable, Callable

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
    NumberDeviceClass,
)
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import PERCENTAGE, DEGREE, UnitOfLength, UnitOfSpeed, AREA_SQUARE_METERS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.device_config import DeviceLimits
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .coordinator import MammotionDataUpdateCoordinator
from .entity import MammotionBaseEntity


@dataclass(frozen=True, kw_only=True)
class MammotionNumberEntityDescription(NumberEntityDescription):
    """Describes Mammotion number entity."""


NUMBER_ENTITIES: tuple[MammotionNumberEntityDescription, ...] = (
    MammotionNumberEntityDescription(
        key="start_progress",
        min_value=0,
        max_value=100,
        step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionNumberEntityDescription(
        key="cutting_angle",
        entity_category=EntityCategory.CONFIG,
        step=1,
        native_unit_of_measurement=DEGREE,
        min_value=-180,
        max_value=180,
    ),
)

YUKA_NUMBER_ENTITIES: tuple[MammotionNumberEntityDescription, ...] = (
    MammotionNumberEntityDescription(
        key="dumping_interval",
        min_value=5,
        max_value=100,
        step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=AREA_SQUARE_METERS,
        entity_category=EntityCategory.CONFIG,
)
)


NUMBER_WORKING_ENTITIES: tuple[MammotionNumberEntityDescription, ...] = (
    MammotionNumberEntityDescription(
        key="blade_height",
        step=5,
        min_value=30,  # ToDo: To be dynamiclly set based on model (h\non H)
        max_value=70,  # ToDo: To be dynamiclly set based on model (h\non H)
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionNumberEntityDescription(
        key="working_speed",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        step=0.1,
        min_value=0.2,
        max_value=0.6,
    ),
    MammotionNumberEntityDescription(
        key="path_spacing",
        entity_category=EntityCategory.CONFIG,
        step=1,
        device_class=NumberDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        min_value=20,
        max_value=35,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion number entities."""
    coordinator = entry.runtime_data
    limits = coordinator.manager.mower(coordinator.device_name).limits

    entities: list[MammotionNumberEntity] = []

    for entity_description in NUMBER_WORKING_ENTITIES:
        entity = MammotionWorkingNumberEntity(coordinator, entity_description, limits)
        entities.append(entity)

    for entity_description in NUMBER_ENTITIES:
        entity = MammotionNumberEntity(coordinator, entity_description)
        entities.append(entity)

    if not DeviceType.is_yuka(coordinator.device_name):
        for entity_description in YUKA_NUMBER_ENTITIES:
            entity = MammotionNumberEntity(coordinator, entity_description)
            entities.append(entity)

    async_add_entities(entities)


class MammotionNumberEntity(MammotionBaseEntity, NumberEntity):
    entity_description: MammotionNumberEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionDataUpdateCoordinator,
        entity_description: MammotionNumberEntityDescription,
    ) -> None:
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_native_min_value = entity_description.min_value
        self._attr_native_max_value = entity_description.max_value
        self._attr_native_step = entity_description.step
        self._attr_native_value = self._attr_native_min_value  # Default value

    async def async_set_native_value(self, value: float | int) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()


class MammotionWorkingNumberEntity(MammotionNumberEntity):
    """Mammotion working number entity."""

    def __init__(
        self,
        coordinator: MammotionDataUpdateCoordinator,
        entity_description: MammotionNumberEntityDescription,
        limits: DeviceLimits,
    ) -> None:
        super().__init__(coordinator, entity_description)

        min_attr = f"{entity_description.key}_min"
        max_attr = f"{entity_description.key}_max"

        if hasattr(limits, min_attr) and hasattr(limits, max_attr):
            self._attr_native_min_value = getattr(limits, min_attr)
            self._attr_native_max_value = getattr(limits, max_attr)
        else:
            # Fallback to the values from entity_description
            self._attr_native_min_value = entity_description.min_value
            self._attr_native_max_value = entity_description.max_value

    @property
    def native_min_value(self) -> float:
        """Return the minimum value."""
        return self._attr_native_min_value

    @property
    def native_max_value(self) -> float:
        """Return the maximum value."""
        return self._attr_native_max_value
