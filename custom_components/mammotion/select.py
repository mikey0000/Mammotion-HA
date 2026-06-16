"""Select entities for the Mammotion integration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
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
from .entity import MammotionBaseEntity, MammotionBaseSpinoEntity


@dataclass(frozen=True, kw_only=True)
class MammotionConfigSelectEntityDescription(SelectEntityDescription):
    """Describes Mammotion select entity."""

    key: str
    options: list[str]
    set_fn: Callable[[MammotionBaseUpdateCoordinator, str], None]
    async_set_fn: Callable[[MammotionBaseUpdateCoordinator], Awaitable[None]] = None


@dataclass(frozen=True, kw_only=True)
class MammotionAsyncConfigSelectEntityDescription(MammotionBaseEntity, SelectEntity):
    """Describes Mammotion select entity with async functionality."""

    key: str
    options: list[str]
    get_fn: Callable[[MammotionBaseUpdateCoordinator], int | None]
    set_fn: Callable[[MammotionBaseUpdateCoordinator, str], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoSelectEntityDescription(SelectEntityDescription):
    """Describes a Mammotion Spino pool cleaner select entity."""

    options: list[str]
    current_fn: Callable[[PoolCleanerDevice], str]
    set_fn: Callable[[MammotionSpinoCoordinator, str], Awaitable[None]]


SPINO_SELECT_ENTITIES: tuple[MammotionSpinoSelectEntityDescription, ...] = (
    MammotionSpinoSelectEntityDescription(
        key="spino_work_mode",
        # Only real cleaning modes are selectable.  RECHARGE (0, return-to-charge)
        # and UNKNOWN (-1, sentinel) are valid *reported* values — surfaced by the
        # spino_work_mode sensor — but they're not modes a user can start, so they
        # are excluded from the select's options.
        options=[
            mode.name
            for mode in SpinoWorkMode
            if mode not in (SpinoWorkMode.UNKNOWN, SpinoWorkMode.RECHARGE)
        ],
        current_fn=lambda spino_data: spino_data.pool_state.work_mode.name,
        set_fn=lambda coordinator, value: coordinator.async_set_work_mode(
            SpinoWorkMode[value].value
        ),
    ),
    MammotionSpinoSelectEntityDescription(
        key="spino_wall_material",
        # UNKNOWN (-1) is a sentinel for an unreported value, not a user choice.
        options=[
            material.name
            for material in WallMaterial
            if material is not WallMaterial.UNKNOWN
        ],
        current_fn=lambda spino_data: spino_data.pool_state.wall_material.name,
        set_fn=lambda coordinator, value: coordinator.async_set_wall_material(
            WallMaterial[value].value
        ),
    ),
    MammotionSpinoSelectEntityDescription(
        key="spino_bottom_type",
        # UNKNOWN (-1) is a sentinel for an unreported value, not a user choice.
        options=[
            bottom.name
            for bottom in PoolBottomType
            if bottom is not PoolBottomType.UNKNOWN
        ],
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
        entities = []

        for entity_description in SELECT_ENTITIES:
            entities.append(
                MammotionConfigSelectEntity(
                    mower.reporting_coordinator, entity_description
                )
            )

        if DeviceType.is_luba_pro(mower.device.device_name):
            for entity_description in AUDIO_SELECT_ENTITIES:
                entities.append(
                    MammotionAsyncConfigSelectEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )

        for entity_description in ASYNC_SELECT_ENTITIES:
            entities.append(
                MammotionAsyncConfigSelectEntity(
                    mower.reporting_coordinator, entity_description
                )
            )

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
            async_set_fn=lambda coordinator: coordinator.async_modify_plan_if_mowing(),
        )
        entities.append(
            MammotionConfigSelectEntity(mower.reporting_coordinator, bypass_mode_desc)
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

    for spino in entry.runtime_data.spino:
        async_add_entities(
            MammotionSpinoSelectEntity(spino.coordinator, entity_description)
            for entity_description in SPINO_SELECT_ENTITIES
        )


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
        self._attr_current_option = entity_description.options[0]
        self.entity_description.set_fn(self.coordinator, self._attr_current_option)

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        self._attr_current_option = option
        self.entity_description.set_fn(self.coordinator, option)
        if self.entity_description.async_set_fn is not None:
            await self.entity_description.async_set_fn(self.coordinator)
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
        """Initialize the async config select entity."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_options = entity_description.options
        self._attr_current_option = self._resolve_option()

    def _resolve_option(self) -> str:
        """Return the current option, falling back to the first if the index is out of range."""
        if callable(self.entity_description.get_fn):
            idx = self.entity_description.get_fn(self.coordinator)
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
        if callable(self.entity_description.get_fn):
            self._attr_current_option = self._attr_options[
                self.entity_description.get_fn(self.coordinator)
            ]
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
