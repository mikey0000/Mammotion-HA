"""Select entities for the Mammotion integration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from pymammotion.data.model.device import PoolCleanerDevice
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
    WildlifeSafety,
)
from pymammotion.data.model.pool_state import (
    PoolBottomType,
    SpinoWorkMode,
    WallMaterial,
)
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry, MammotionReportUpdateCoordinator
from .coordinator import MammotionBaseUpdateCoordinator, MammotionSpinoCoordinator
from .const import DOMAIN
from .entity import MammotionBaseEntity, MammotionBaseSpinoEntity
from .yuka import (
    BLADE_SPEED_OPTIONS,
    BLADE_SPEED_VALUES,
    GRID_PATTERN_OPTIONS,
    GRID_PATTERN_VALUE,
    LAP_OPTIONS,
    LAP_VALUES,
    OBSTACLE_OPTIONS,
    OBSTACLE_VALUES,
    PATTERN_FAMILY_OPTIONS,
    PATTERN_FAMILY_VALUES,
    ROUTE_TO_DOCK_OPTIONS,
    ROUTE_TO_DOCK_VALUES,
    STRIPES_PATTERN_OPTIONS,
    STRIPES_PATTERN_VALUE,
    VOICE_VOLUME_LEVEL_OPTIONS,
    VOICE_VOLUME_VALUES,
    WILDLIFE_SAFETY_OPTIONS,
    WILDLIFE_SAFETY_VALUES,
    is_yuka_2,
    is_yuka_mini_or_ml,
    voice_volume_option,
    yuka_value_option,
)


@dataclass(frozen=True, kw_only=True)
class MammotionConfigSelectEntityDescription(SelectEntityDescription):
    """Describes Mammotion select entity."""

    key: str
    options: list[str]
    set_fn: Callable[[MammotionBaseUpdateCoordinator, str], None]
    get_fn: Callable[[MammotionBaseUpdateCoordinator], str | None] | None = None
    default_option: str | None = None
    async_set_fn: (
        Callable[[MammotionBaseUpdateCoordinator], Awaitable[None]] | None
    ) = None
    available_fn: Callable[[MammotionBaseUpdateCoordinator], bool] | None = None


@dataclass(frozen=True, kw_only=True)
class MammotionAsyncConfigSelectEntityDescription(MammotionBaseEntity, SelectEntity):
    """Describes Mammotion select entity with async functionality."""

    key: str
    options: list[str]
    get_fn: Callable[[MammotionBaseUpdateCoordinator], int | str | None]
    set_fn: Callable[[MammotionBaseUpdateCoordinator, str], Awaitable[None]]
    poll_fn: Callable[[MammotionBaseUpdateCoordinator], Awaitable[None]] | None = None


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoSelectEntityDescription(SelectEntityDescription):
    """Describes a Mammotion Spino pool cleaner select entity."""

    options: list[str]
    current_fn: Callable[[PoolCleanerDevice], str]
    set_fn: Callable[[MammotionSpinoCoordinator, str], Awaitable[None]]


SPINO_SELECT_ENTITIES: tuple[MammotionSpinoSelectEntityDescription, ...] = (
    MammotionSpinoSelectEntityDescription(
        key="spino_work_mode",
        options=[mode.name for mode in SpinoWorkMode],
        current_fn=lambda spino_data: spino_data.pool_state.work_mode.name,
        set_fn=lambda coordinator, value: coordinator.async_set_work_mode(
            SpinoWorkMode[value].value
        ),
    ),
    MammotionSpinoSelectEntityDescription(
        key="spino_wall_material",
        options=[material.name for material in WallMaterial],
        current_fn=lambda spino_data: spino_data.pool_state.wall_material.name,
        set_fn=lambda coordinator, value: coordinator.async_set_wall_material(
            WallMaterial[value].value
        ),
    ),
    MammotionSpinoSelectEntityDescription(
        key="spino_bottom_type",
        options=[bottom.name for bottom in PoolBottomType],
        current_fn=lambda spino_data: spino_data.pool_state.bottom_type.name,
        set_fn=lambda coordinator, value: coordinator.async_set_bottom_type(
            PoolBottomType[value].value
        ),
    ),
)


AUDIO_SELECT_ENTITIES: tuple[MammotionAsyncConfigSelectEntityDescription, ...] = (
    MammotionAsyncConfigSelectEntityDescription(
        key="voice_gender",
        options=["MAN", "WOMAN"],
        get_fn=lambda coordinator: coordinator.data.mower_state.audio.sex,
        set_fn=lambda coordinator, value: coordinator.async_set_voice_gender(value),
    ),
)

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
    MammotionAsyncConfigSelectEntityDescription(
        key="wildlife_safety",
        options=[mode.name for mode in WildlifeSafety],
        get_fn=lambda coordinator: (
            WildlifeSafety.off.value
            if coordinator.data.mower_state.animal_protection.status == 0
            else coordinator.data.mower_state.animal_protection.mode
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_wildlife_safety(
            WildlifeSafety[value].value
        ),
    ),
)

MINI_AND_X_SERIES_CONFIG_SELECT_ENTITIES: tuple[
    MammotionAsyncConfigSelectEntityDescription, ...
] = (
    MammotionAsyncConfigSelectEntityDescription(
        key="cutter_mode",
        options=[mode.name for mode in CuttingSpeedMode],
        get_fn=lambda coordinator: coordinator.data.mower_state.cutter_mode,
        set_fn=lambda coordinator, value: coordinator.async_set_cutter_speed(
            CuttingSpeedMode[value].value
        ),
    ),
)


def _set_pattern_family(
    coordinator: MammotionBaseUpdateCoordinator, option: str
) -> None:
    """Set the app-level Yuka mowing pattern family."""
    coordinator.operation_settings.channel_mode = PATTERN_FAMILY_VALUES[option]


def _set_pattern_variant(
    coordinator: MammotionBaseUpdateCoordinator, option: str
) -> None:
    """Set the app-level Yuka path angle variant."""
    if option in {"efficient", "default"}:
        coordinator.operation_settings.toward_mode = (
            PathAngleSetting.relative_angle.value
        )
    elif option == "random":
        coordinator.operation_settings.toward_mode = PathAngleSetting.random_angle.value
    elif option == "custom":
        coordinator.operation_settings.toward_mode = (
            PathAngleSetting.absolute_angle.value
        )


def _set_stripes_pattern(
    coordinator: MammotionBaseUpdateCoordinator, option: str
) -> None:
    """Set the stripes variant only when stripes are active."""
    if coordinator.operation_settings.channel_mode == STRIPES_PATTERN_VALUE:
        _set_pattern_variant(coordinator, option)


def _set_grid_pattern(coordinator: MammotionBaseUpdateCoordinator, option: str) -> None:
    """Set the grid variant only when grid is active."""
    if coordinator.operation_settings.channel_mode == GRID_PATTERN_VALUE:
        _set_pattern_variant(coordinator, option)


def _set_yuka_mow_order(
    coordinator: MammotionBaseUpdateCoordinator, option: str
) -> None:
    """Set the app-level Yuka mow order."""
    value = MowOrder[option].value
    coordinator.operation_settings.border_mode = value
    coordinator.operation_settings.job_mode = value


def _value_option(options: list[str], value: int, values: dict[str, int]) -> str:
    """Return the option matching a numeric operation setting."""
    for option, option_value in values.items():
        if option_value == value and option in options:
            return option
    return options[0]


def _get_yuka_mow_order(coordinator: MammotionBaseUpdateCoordinator) -> str:
    """Return the selected Yuka mow order from operation settings."""
    values = {order.name: order.value for order in MowOrder}
    return _value_option(
        [order.name for order in MowOrder], coordinator.operation_settings.job_mode, values
    )


def _get_yuka_lap(
    coordinator: MammotionBaseUpdateCoordinator, setting: str
) -> str:
    """Return the selected Yuka lap option from operation settings."""
    return _value_option(
        LAP_OPTIONS, getattr(coordinator.operation_settings, setting), LAP_VALUES
    )


def _get_yuka_obstacle_detection(
    coordinator: MammotionBaseUpdateCoordinator,
) -> str:
    """Return the selected Yuka obstacle detection option."""
    return _value_option(
        OBSTACLE_OPTIONS,
        coordinator.operation_settings.ultra_wave,
        OBSTACLE_VALUES,
    )


def _get_pattern_family(coordinator: MammotionBaseUpdateCoordinator) -> str:
    """Return the selected Yuka pattern family from operation settings."""
    return _value_option(
        PATTERN_FAMILY_OPTIONS,
        coordinator.operation_settings.channel_mode,
        PATTERN_FAMILY_VALUES,
    )


def _get_pattern_variant(
    coordinator: MammotionBaseUpdateCoordinator, options: list[str]
) -> str:
    """Return the selected app-level pattern variant from operation settings."""
    value = coordinator.operation_settings.toward_mode
    if value == PathAngleSetting.relative_angle.value:
        return "efficient" if "efficient" in options else "default"
    if value == PathAngleSetting.random_angle.value:
        return "random"
    if value == PathAngleSetting.absolute_angle.value:
        return "custom"
    return options[0]


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

YUKA_SELECT_ENTITIES: tuple[MammotionConfigSelectEntityDescription, ...] = (
    MammotionConfigSelectEntityDescription(
        key="border_mode",
        options=[order.name for order in MowOrder],
        default_option=MowOrder.border_first.name,
        get_fn=_get_yuka_mow_order,
        set_fn=_set_yuka_mow_order,
    ),
    MammotionConfigSelectEntityDescription(
        key="mowing_laps",
        options=LAP_OPTIONS,
        default_option="three",
        get_fn=lambda coordinator: _get_yuka_lap(coordinator, "mowing_laps"),
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "mowing_laps", LAP_VALUES[value]
        ),
    ),
    MammotionConfigSelectEntityDescription(
        key="obstacle_laps",
        options=LAP_OPTIONS,
        default_option="none",
        get_fn=lambda coordinator: _get_yuka_lap(coordinator, "obstacle_laps"),
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "obstacle_laps", LAP_VALUES[value]
        ),
    ),
    MammotionConfigSelectEntityDescription(
        key="bypass_mode",
        options=OBSTACLE_OPTIONS,
        default_option="standard",
        get_fn=_get_yuka_obstacle_detection,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "ultra_wave", OBSTACLE_VALUES[value]
        ),
        async_set_fn=lambda coordinator: coordinator.async_modify_plan_if_mowing(),
    ),
    MammotionConfigSelectEntityDescription(
        key="pattern_family",
        options=PATTERN_FAMILY_OPTIONS,
        default_option="grid",
        get_fn=_get_pattern_family,
        set_fn=_set_pattern_family,
        async_set_fn=lambda coordinator: coordinator.async_modify_plan_if_mowing(),
    ),
    MammotionConfigSelectEntityDescription(
        key="stripes_pattern",
        options=STRIPES_PATTERN_OPTIONS,
        default_option="random",
        get_fn=lambda coordinator: _get_pattern_variant(
            coordinator, STRIPES_PATTERN_OPTIONS
        ),
        set_fn=_set_stripes_pattern,
        async_set_fn=lambda coordinator: coordinator.async_modify_plan_if_mowing(),
        available_fn=lambda coordinator: coordinator.operation_settings.channel_mode
        == STRIPES_PATTERN_VALUE,
    ),
    MammotionConfigSelectEntityDescription(
        key="grid_pattern",
        options=GRID_PATTERN_OPTIONS,
        default_option="random",
        get_fn=lambda coordinator: _get_pattern_variant(
            coordinator, GRID_PATTERN_OPTIONS
        ),
        set_fn=_set_grid_pattern,
        async_set_fn=lambda coordinator: coordinator.async_modify_plan_if_mowing(),
        available_fn=lambda coordinator: coordinator.operation_settings.channel_mode
        == GRID_PATTERN_VALUE,
    ),
)

YUKA_ASYNC_SELECT_ENTITIES: tuple[MammotionAsyncConfigSelectEntityDescription, ...] = (
    MammotionAsyncConfigSelectEntityDescription(
        key="traversal_mode",
        options=ROUTE_TO_DOCK_OPTIONS,
        get_fn=lambda coordinator: yuka_value_option(
            ROUTE_TO_DOCK_OPTIONS,
            coordinator.data.mower_state.traversal_mode,
            ROUTE_TO_DOCK_VALUES,
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_traversal_mode(
            ROUTE_TO_DOCK_VALUES[value]
        ),
    ),
    MammotionAsyncConfigSelectEntityDescription(
        key="cutter_mode",
        options=BLADE_SPEED_OPTIONS,
        get_fn=lambda coordinator: yuka_value_option(
            BLADE_SPEED_OPTIONS,
            coordinator.data.mower_state.cutter_mode,
            BLADE_SPEED_VALUES,
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_cutter_speed(
            BLADE_SPEED_VALUES[value]
        ),
    ),
    MammotionAsyncConfigSelectEntityDescription(
        key="voice_volume_level",
        options=VOICE_VOLUME_LEVEL_OPTIONS,
        get_fn=lambda coordinator: voice_volume_option(
            coordinator.data.mower_state.audio.volume
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_voice_volume(
            VOICE_VOLUME_VALUES[value]
        ),
    ),
    MammotionAsyncConfigSelectEntityDescription(
        key="wildlife_safety",
        options=WILDLIFE_SAFETY_OPTIONS,
        get_fn=lambda coordinator: yuka_value_option(
            WILDLIFE_SAFETY_OPTIONS,
            coordinator.data.mower_state.animal_protection.mode,
            WILDLIFE_SAFETY_VALUES,
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_wildlife_safety(
            WILDLIFE_SAFETY_VALUES[value]
        ),
        poll_fn=lambda coordinator: coordinator.async_read_wildlife_safety(),
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
)

LUBA_PRO_SELECT_ENTITIES: tuple[MammotionConfigSelectEntityDescription, ...] = (
    MammotionConfigSelectEntityDescription(
        key="cutting_angle_mode",
        options=[angle_type.name for angle_type in PathAngleSetting],
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "toward_mode", PathAngleSetting[value].value
        ),
    ),
)


def _device_firmware_version(device_state: object | None) -> str:
    """Return the runtime device firmware version, or "" when unknown.

    Firmware lives on the runtime device state (MowerDevice/RTK/Pool), reached via
    the coordinator's data — NOT on ``mower.device``, which is the Aliyun
    list/binding response model and has no ``device_firmwares``. Coordinator data
    can also be ``None`` or a bare ``Device`` before telemetry arrives, so default
    to "" (DetectionStrategy.for_device treats empty as the new-firmware options).
    """
    device_firmwares = getattr(device_state, "device_firmwares", None)
    return device_firmwares.device_version if device_firmwares is not None else ""


# Define the setup entry function
async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion select entity."""
    mammotion_devices = entry.runtime_data.mowers

    for mower in mammotion_devices:
        _cleanup_removed_yuka_2_selects(hass, mower.device.device_name)
        entities = []

        base_selects = (
            YUKA_SELECT_ENTITIES
            if is_yuka_2(mower.device.device_name)
            else SELECT_ENTITIES
        )
        for entity_description in base_selects:
            entities.append(
                MammotionConfigSelectEntity(
                    mower.reporting_coordinator, entity_description
                )
            )

        if DeviceType.is_luba_pro(mower.device.device_name) and not is_yuka_2(
            mower.device.device_name
        ):
            for entity_description in AUDIO_SELECT_ENTITIES:
                entities.append(
                    MammotionAsyncConfigSelectEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )

        async_selects = (
            YUKA_ASYNC_SELECT_ENTITIES
            if is_yuka_2(mower.device.device_name)
            else ASYNC_SELECT_ENTITIES
        )
        for entity_description in async_selects:
            entities.append(
                MammotionAsyncConfigSelectEntity(
                    mower.reporting_coordinator, entity_description
                )
            )

        if not is_yuka_2(mower.device.device_name):
            bypass_mode_desc = MammotionConfigSelectEntityDescription(
                key="bypass_mode",
                options=[
                    s.name
                    for s in DetectionStrategy.for_device(
                        mower.device.device_name,
                        _device_firmware_version(mower.reporting_coordinator.data),
                    )
                ],
                set_fn=lambda coordinator, value: setattr(
                    coordinator.operation_settings,
                    "ultra_wave",
                    DetectionStrategy[value].value,
                ),
                async_set_fn=(
                    lambda coordinator: coordinator.async_modify_plan_if_mowing()
                ),
            )
            entities.append(
                MammotionConfigSelectEntity(
                    mower.reporting_coordinator, bypass_mode_desc
                )
            )

        if DeviceType.is_luba1(mower.device.device_name):
            for entity_description in LUBA1_SELECT_ENTITIES:
                entities.append(
                    MammotionConfigSelectEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )
        elif not is_yuka_mini_or_ml(mower.device.device_name):
            for entity_description in LUBA_PRO_SELECT_ENTITIES:
                entities.append(
                    MammotionConfigSelectEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )

        if DeviceType.is_mini_or_x_series(
            mower.device.device_name
        ) and not is_yuka_2(mower.device.device_name):
            for entity_description in MINI_AND_X_SERIES_CONFIG_SELECT_ENTITIES:
                entities.append(
                    MammotionAsyncConfigSelectEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )

        async_add_entities(entities)

    for spino in entry.runtime_data.spino:
        async_add_entities(
            MammotionSpinoSelectEntity(spino.coordinator, entity_description)
            for entity_description in SPINO_SELECT_ENTITIES
        )


def _cleanup_removed_yuka_2_selects(hass: HomeAssistant, device_name: str) -> None:
    """Remove select entities that are not exposed by the Yuka app controls."""
    if not is_yuka_2(device_name):
        return
    registry = er.async_get(hass)
    for key in (
        "channel_mode",
        "cutting_angle_mode",
        "turning_mode",
        "voice_gender",
    ):
        entity_id = registry.async_get_entity_id(
            "select", DOMAIN, f"{device_name}_{key}"
        )
        if entity_id:
            registry.async_remove(entity_id)


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
        """Initialize the config select entity."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_options = entity_description.options
        self._attr_current_option = (
            entity_description.default_option or entity_description.options[0]
        )
        self._attr_current_option = self._resolve_option()
        if self.entity_description.get_fn is None:
            self.entity_description.set_fn(self.coordinator, self._attr_current_option)

    def _resolve_option(self) -> str:
        """Return the current option from coordinator operation settings."""
        if self.entity_description.get_fn is not None:
            option = self.entity_description.get_fn(self.coordinator)
            if option in self._attr_options:
                return option
        return self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        self._attr_current_option = option
        self.entity_description.set_fn(self.coordinator, option)
        if self.entity_description.async_set_fn is not None:
            await self.entity_description.async_set_fn(self.coordinator)
        self.coordinator.async_update_listeners()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_current_option = self._resolve_option()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Restore last state."""
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            if state.state in self.entity_description.options:
                self._attr_current_option = state.state
                self.entity_description.set_fn(
                    self.coordinator, self._attr_current_option
                )
                self.coordinator.async_update_listeners()

    @property
    def available(self) -> bool:
        """Return True when this select applies to the current Yuka settings."""
        if self.entity_description.available_fn is None:
            return super().available
        return super().available and self.entity_description.available_fn(
            self.coordinator
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
        """Initialize the async config select entity."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_options = entity_description.options
        self._attr_current_option = self._resolve_option()

    def _resolve_option(self) -> str:
        """Return the current option, falling back to the first option."""
        if callable(self.entity_description.get_fn):
            value = self.entity_description.get_fn(self.coordinator)
            if isinstance(value, str) and value in self._attr_options:
                return value
            try:
                idx = int(value)
            except (TypeError, ValueError):
                return self._attr_options[0]
            if 0 <= idx < len(self._attr_options):
                return self._attr_options[idx]
        return self._attr_options[0]

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_current_option = self._resolve_option()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
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
        """Update entity state from coordinator."""
        if self.entity_description.poll_fn is not None:
            await self.entity_description.poll_fn(self.coordinator)
        if callable(self.entity_description.get_fn):
            self._attr_current_option = self._resolve_option()
        self.async_write_ha_state()


class MammotionSpinoSelectEntity(MammotionBaseSpinoEntity, SelectEntity):
    """Representation of a Mammotion Spino pool cleaner select entity."""

    _attr_entity_category = EntityCategory.CONFIG

    entity_description: MammotionSpinoSelectEntityDescription

    def __init__(
        self,
        coordinator: MammotionSpinoCoordinator,
        entity_description: MammotionSpinoSelectEntityDescription,
    ) -> None:
        """Initialize the Spino select entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_options = entity_description.options

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        return self.entity_description.current_fn(self.coordinator.data)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.entity_description.set_fn(self.coordinator, option)
        await self.coordinator.async_request_refresh()
