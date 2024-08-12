from typing import Callable, Awaitable

from dataclasses import dataclass
from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.device_config import DeviceLimits

from . import MammotionConfigEntry
from .coordinator import MammotionDataUpdateCoordinator

from .entity import MammotionBaseEntity


@dataclass(frozen=True, kw_only=True)
class MammotionNumberEntityDescription(NumberEntityDescription):
    """Describes Mammotion number entity."""
    set_fn: Callable[[HomeAssistant, float], Awaitable[None]]


NUMBER_ENTITIES: tuple[MammotionNumberEntityDescription, ...] = (
    MammotionNumberEntityDescription(
        key="start_progress",
        min_value=0,
        max_value=100,
        step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        set_fn=lambda value: value,
    ),
)


@dataclass
class MammotionWorkingNumberEntityDescription(NumberEntityDescription):
    """Describes Mammotion number entity."""
    set_fn: Callable[[HomeAssistant, float], Awaitable[None]]


NUMBER_WORKING_ENTITIES: tuple[MammotionWorkingNumberEntityDescription, ...] = (
    MammotionWorkingNumberEntityDescription(
        key="cutter_height",
        step=5.0,
        entity_category=EntityCategory.CONFIG,
        set_fn=lambda coordinator, value: coordinator.async_blade_height(value),
    ),
    MammotionWorkingNumberEntityDescription(
        key="working_speed",
        entity_category=EntityCategory.CONFIG,
        step=0.1,
        set_fn=lambda value: value,
    ),
)


# Example setup usage
async def async_setup_entry(
        hass: HomeAssistant,
        entry: MammotionConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion number entity."""
    # Example limits, replace this with actual coordinator data
    coordinator = entry.runtime_data

    limits = coordinator.device.limits

    entities: list[MammotionNumberEntity] = [
        MammotionWorkingNumberEntity(coordinator, entity_description, limits)
        for entity_description in NUMBER_WORKING_ENTITIES
    ]

    for entity_description in NUMBER_ENTITIES:
        entities.append(MammotionNumberEntity(coordinator, entity_description))

    async_add_entities(
        entities
    )


class MammotionNumberEntity(MammotionBaseEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG

    entity_description: MammotionNumberEntityDescription
    _attr_has_entity_name = True

    def __init__(self,
                 coordinator: MammotionDataUpdateCoordinator,
                 entity_description: MammotionNumberEntityDescription) -> None:
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_min_value = entity_description.min_value
        self._attr_max_value = entity_description.max_value
        self._attr_step = entity_description.step
        # TODO populate with sane defaults
        self._attr_value = self._attr_min_value  # Default value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        await self.entity_description.set_fn(self.hass, value)
        self.async_write_ha_state()


class MammotionWorkingNumberEntity(MammotionNumberEntity):
    """Mammotion working number entity."""
    entity_description: MammotionWorkingNumberEntityDescription
    _attr_has_entity_name = True

    def __init__(self, entity_description: MammotionNumberEntityDescription,
                 coordinator: MammotionDataUpdateCoordinator, limits: DeviceLimits) -> None:
        super().__init__(coordinator, entity_description)

        self._attr_min_value = limits[f"{entity_description.key}_min"]
        self._attr_max_value = limits[f"{entity_description.key}_max"]
