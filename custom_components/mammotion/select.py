from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from pymammotion.data.model.mowing_modes import (
    BorderPatrolMode,
    CuttingMode,
    CuttingSpeedMode,
    DetectionStrategy,
    MowOrder,
    ObstacleLapsMode,
    PathAngleSetting,
    TraversalMode,
    TurningMode,
)
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry, MammotionReportUpdateCoordinator
from .coordinator import MammotionBaseUpdateCoordinator
from .entity import MammotionBaseEntity


@dataclass(frozen=True, kw_only=True)
class MammotionConfigSelectEntityDescription(SelectEntityDescription):
    """Describes Mammotion select entity."""

    key: str
    options: list[str]
    set_fn: Callable[[MammotionBaseUpdateCoordinator, str], None]


@dataclass(frozen=True, kw_only=True)
class MammotionAsyncConfigSelectEntityDescription(MammotionBaseEntity, SelectEntity):
    """Describes Mammotion select entity with async functionality."""

    key: str
    options: list[str]
    get_fn: Callable[[MammotionBaseUpdateCoordinator], int | None]
    set_fn: Callable[[MammotionBaseUpdateCoordinator, str], Awaitable[None]]


ASYNC_SELECT_ENTITIES: tuple[MammotionAsyncConfigSelectEntityDescription, ...] = (
    MammotionAsyncConfigSelectEntityDescription(
        key="traversal_mode",
        options=[mode.name for mode in TraversalMode],
        get_fn=lambda coordinator: coordinator.data.mower_state.traversal_mode,
        set_fn=lambda coordinator, value: coordinator.async_set_traversal_mode(
            TraversalMode[value].value
        ),
    ),
    MammotionAsyncConfigSelectEntityDescription(
        key="turning_mode",
        options=[mode.name for mode in TurningMode],
        get_fn=lambda coordinator: coordinator.data.mower_state.turning_mode,
        set_fn=lambda coordinator, value: coordinator.async_set_turning_mode(
            TurningMode[value].value
        ),
    ),
)

MINI_AND_X_SERIES_CONFIG_SELECT_ENTITIES: tuple[
    MammotionAsyncConfigSelectEntityDescription, ...
] = (
    MammotionAsyncConfigSelectEntityDescription(
        key="cutter_mode",
        options=[mode.name for mode in CuttingSpeedMode],
        get_fn=lambda coordinator: coordinator.data.mower_state.blade_mode,
        set_fn=lambda coordinator, value: coordinator.async_set_cutter_speed(
            CuttingSpeedMode[value].value
        ),
    ),
)


SELECT_ENTITIES: tuple[MammotionConfigSelectEntityDescription, ...] = (
    MammotionConfigSelectEntityDescription(
        key="channel_mode",
        options=[mode.name for mode in CuttingMode],
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "channel_mode", CuttingMode[value].value
        ),
    ),
    MammotionConfigSelectEntityDescription(
        key="mowing_laps",
        options=[mode.name for mode in BorderPatrolMode],
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "mowing_laps", BorderPatrolMode[value].value
        ),
    ),
    MammotionConfigSelectEntityDescription(
        key="obstacle_laps",
        options=[mode.name for mode in ObstacleLapsMode],
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings,
            "obstacle_laps",
            ObstacleLapsMode[value].value,
        ),
    ),
    MammotionConfigSelectEntityDescription(
        key="border_mode",
        options=[order.name for order in MowOrder],
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "border_mode", MowOrder[value].value
        ),
    ),
)

LUBA1_SELECT_ENTITIES: tuple[MammotionConfigSelectEntityDescription, ...] = (
    MammotionConfigSelectEntityDescription(
        key="cutting_angle_mode",
        options=[
            angle_type.name
            for angle_type in PathAngleSetting
            if angle_type != PathAngleSetting.random_angle
        ],
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "toward_mode", PathAngleSetting[value].value
        ),
    ),
    MammotionConfigSelectEntityDescription(
        key="bypass_mode",
        options=[
            strategy.name
            for strategy in DetectionStrategy
            if strategy != DetectionStrategy.no_touch
        ],
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "ultra_wave", DetectionStrategy[value].value
        ),
    ),
)

LUBA_PRO_SELECT_ENTITIES: tuple[MammotionConfigSelectEntityDescription, ...] = (
    MammotionConfigSelectEntityDescription(
        key="cutting_angle_mode",
        options=[angle_type.name for angle_type in PathAngleSetting],
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "toward_mode", PathAngleSetting[value].value
        ),
    ),
    MammotionConfigSelectEntityDescription(
        key="bypass_mode",
        options=[strategy.name for strategy in DetectionStrategy],
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "ultra_wave", DetectionStrategy[value].value
        ),
    ),
)


# Define the setup entry function
async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion select entity."""
    mammotion_devices = entry.runtime_data.mowers

    for mower in mammotion_devices:
        entities = []

        for entity_description in SELECT_ENTITIES:
            entities.append(
                MammotionConfigSelectEntity(
                    mower.reporting_coordinator, entity_description
                )
            )

        for entity_description in ASYNC_SELECT_ENTITIES:
            entities.append(
                MammotionAsyncConfigSelectEntity(
                    mower.reporting_coordinator, entity_description
                )
            )

        if DeviceType.is_luba1(mower.device.device_name):
            for entity_description in LUBA1_SELECT_ENTITIES:
                entities.append(
                    MammotionConfigSelectEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )
        else:
            for entity_description in LUBA_PRO_SELECT_ENTITIES:
                entities.append(
                    MammotionConfigSelectEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )

        if DeviceType.is_mini_or_x_series(mower.device.device_name):
            for entity_description in MINI_AND_X_SERIES_CONFIG_SELECT_ENTITIES:
                entities.append(
                    MammotionAsyncConfigSelectEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )

        async_add_entities(entities)


# Define the select entity class with entity_category: config
class MammotionConfigSelectEntity(MammotionBaseEntity, SelectEntity, RestoreEntity):
    """Representation of a Mammotion select entities."""

    _attr_entity_category = EntityCategory.CONFIG

    entity_description: MammotionConfigSelectEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionReportUpdateCoordinator,
        entity_description: MammotionConfigSelectEntityDescription,
    ) -> None:
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_options = entity_description.options
        self._attr_current_option = entity_description.options[0]
        self.entity_description.set_fn(self.coordinator, self._attr_current_option)

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.entity_description.set_fn(self.coordinator, option)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last state."""
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            if state.state in self.entity_description.options:
                self._attr_current_option = state.state
                self.entity_description.set_fn(
                    self.coordinator, self._attr_current_option
                )


# Define the select entity class with entity_category: config
class MammotionAsyncConfigSelectEntity(
    MammotionBaseEntity, SelectEntity, RestoreEntity
):
    """Representation of a Mammotion select entities."""

    _attr_entity_category = EntityCategory.CONFIG

    entity_description: MammotionAsyncConfigSelectEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionReportUpdateCoordinator,
        entity_description: MammotionAsyncConfigSelectEntityDescription,
    ) -> None:
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_options = entity_description.options
        if callable(entity_description.get_fn):
            self._attr_current_option = self._attr_options[
                entity_description.get_fn(self.coordinator)
            ]
        else:
            self._attr_current_option = self._attr_options[0]

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        await self.entity_description.set_fn(self.coordinator, option)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last state."""
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            if state.state in self.entity_description.options:
                self._attr_current_option = state.state

    async def async_update(self) -> None:
        if callable(self.entity_description.get_fn):
            self._attr_current_option = self._attr_options[
                self.entity_description.get_fn(self.coordinator)
            ]
        self.async_write_ha_state()
