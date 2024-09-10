from dataclasses import dataclass
from typing import Awaitable, Callable

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.mowing_modes import (
    BorderPatrolMode,
    CuttingMode,
    MowOrder,
    ObstacleLapsMode,
    BypassStrategy,
    PathAngleSetting,
)

from . import MammotionConfigEntry
from .coordinator import MammotionDataUpdateCoordinator
from .entity import MammotionBaseEntity


@dataclass(frozen=True, kw_only=True)
class MammotionConfigSelectEntityDescription(SelectEntityDescription):
    """Describes Mammotion select entity."""

    key: str
    options: list[str]
    set_fn: Callable[[MammotionDataUpdateCoordinator, str], None]


SELECT_ENTITIES: tuple[MammotionConfigSelectEntityDescription, ...] = (
    MammotionConfigSelectEntityDescription(
        key="cutting_mode",
        options=[mode.name for mode in CuttingMode],
        set_fn=lambda coordinator, value: setattr(coordinator.operation_settings, 'channel_mode', CuttingMode[value])
    ),
    MammotionConfigSelectEntityDescription(
        key="border_patrol_mode",
        options=[mode.name for mode in BorderPatrolMode],
        set_fn=lambda coordinator, value: setattr(coordinator.operation_settings, 'border_mode', BorderPatrolMode[value])
    ),
    MammotionConfigSelectEntityDescription(
        key="obstacle_laps_mode",
        options=[mode.name for mode in ObstacleLapsMode],
        set_fn=lambda coordinator, value: setattr(coordinator.operation_settings, 'obstacle_laps', ObstacleLapsMode[value])
    ),
    MammotionConfigSelectEntityDescription(
        key="mow_order",
        options=[order.name for order in MowOrder],
        set_fn=lambda coordinator, value: setattr(coordinator.operation_settings, 'job_mode', MowOrder[value])
    ),
    MammotionConfigSelectEntityDescription(
        key="bypass_mode",
        options=[strategy.name for strategy in BypassStrategy],
        set_fn=lambda coordinator, value: setattr(coordinator.operation_settings, 'ultra_wave', BypassStrategy[value])
    ),
    MammotionConfigSelectEntityDescription(
        key="cutting_angle_mode",
        options=[angle_type.name for angle_type in PathAngleSetting],
        set_fn=lambda coordinator, value: setattr(coordinator.operation_settings, 'toward_mode', PathAngleSetting[value])
    ),
)


# Define the setup entry function
async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion select entity."""
    coordinator = entry.runtime_data

    async_add_entities(
        MammotionConfigSelectEntity(coordinator, entity_description)
        for entity_description in SELECT_ENTITIES
    )


# Define the select entity class with entity_category: config
class MammotionConfigSelectEntity(MammotionBaseEntity, SelectEntity):
    """Representation of a Mammotion select entities."""

    _attr_entity_category = EntityCategory.CONFIG

    entity_description: MammotionConfigSelectEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionDataUpdateCoordinator,
        entity_description: MammotionConfigSelectEntityDescription,
    ) -> None:
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_options = entity_description.options
        self._attr_current_option = entity_description.options[0]

    async def async_select_option(self, option: str) -> None:
        self.set_fn(self.coordinator, option)