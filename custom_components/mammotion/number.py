"""Number entities for the Mammotion integration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    UnitOfLength,
    UnitOfSpeed,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.device import PoolCleanerDevice
from pymammotion.data.model.device_limits import DeviceLimits
from pymammotion.data.model.mowing_modes import PathAngleSetting
from pymammotion.utility.device_config import DeviceConfig
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .coordinator import MammotionBaseUpdateCoordinator, MammotionSpinoCoordinator
from .const import DOMAIN
from .entity import MammotionBaseEntity, MammotionBaseSpinoEntity
from .yuka import (
    GRID_PATTERN_VALUE,
    STRIPES_PATTERN_VALUE,
    is_yuka_2,
    is_yuka_mini_or_ml,
)


@dataclass(frozen=True, kw_only=True)
class MammotionConfigNumberEntityDescription(NumberEntityDescription):  # type: ignore[misc]
    """Describes Mammotion number entity."""

    set_fn: Callable[[MammotionBaseUpdateCoordinator[Any], float], None] | None = None
    set_async_fn: (
        Callable[[MammotionBaseUpdateCoordinator[Any], float], Awaitable[None]] | None
    ) = None
    get_fn: Callable[[MammotionBaseUpdateCoordinator[Any]], float | None] | None = None
    available_fn: Callable[[MammotionBaseUpdateCoordinator[Any]], bool] | None = None


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoNumberEntityDescription(NumberEntityDescription):  # type: ignore[misc]
    """Describes a Mammotion Spino pool cleaner number entity."""

    value_fn: Callable[[PoolCleanerDevice], float]
    set_fn: Callable[[MammotionSpinoCoordinator, float], Awaitable[None]]


SPINO_NUMBER_ENTITIES: tuple[MammotionSpinoNumberEntityDescription, ...] = (
    MammotionSpinoNumberEntityDescription(
        key="spino_floor_speed",
        native_min_value=0.1,
        native_max_value=1.0,
        native_step=0.05,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda spino_data: spino_data.pool_state.floor_speed,
        set_fn=lambda coordinator, value: coordinator.async_set_floor_speed(value),
    ),
)


MAP_OFFSET_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="map_offset_lat",
        device_class=NumberDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        native_step=0.1,
        native_min_value=-50,
        native_max_value=50,
        mode=NumberMode.BOX,
        set_fn=lambda coordinator, value: setattr(coordinator, "map_offset_lat", value),
        get_fn=lambda coordinator: coordinator.map_offset_lat,
    ),
    MammotionConfigNumberEntityDescription(
        key="map_offset_lon",
        device_class=NumberDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        native_step=0.1,
        native_min_value=-50,
        native_max_value=50,
        mode=NumberMode.BOX,
        set_fn=lambda coordinator, value: setattr(coordinator, "map_offset_lon", value),
        get_fn=lambda coordinator: coordinator.map_offset_lon,
    ),
)

AUDIO_NUMBER_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="voice_volume",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        set_async_fn=lambda coordinator, value: coordinator.async_set_voice_volume(
            value
        ),
        get_fn=lambda coordinator: coordinator.data.mower_state.audio.volume,
    ),
)

NUMBER_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="start_progress",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "start_progress", value
        ),
    ),
    MammotionConfigNumberEntityDescription(
        key="cutting_angle",
        native_step=1,
        native_unit_of_measurement=DEGREE,
        native_min_value=-180,
        native_max_value=180,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "toward", value
        ),
    ),
    MammotionConfigNumberEntityDescription(
        key="toward_included_angle",
        native_step=1,
        native_unit_of_measurement=DEGREE,
        native_min_value=-180,
        native_max_value=180,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "toward_included_angle", value
        ),
    ),
)

YUKA_NUMBER_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="working_speed",
        device_class=NumberDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        native_step=0.1,
        native_min_value=0.2,
        native_max_value=0.6,
        set_async_fn=lambda coordinator,
        value: coordinator.async_modify_plan_if_mowing(),
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "speed", value
        ),
        get_fn=lambda coordinator: coordinator.operation_settings.speed,
    ),
    MammotionConfigNumberEntityDescription(
        key="path_spacing",
        native_step=1,
        device_class=NumberDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        native_min_value=8,
        native_max_value=12,
        set_async_fn=lambda coordinator,
        value: coordinator.async_modify_plan_if_mowing(),
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "channel_width", value
        ),
        get_fn=lambda coordinator: coordinator.operation_settings.channel_width,
    ),
    MammotionConfigNumberEntityDescription(
        key="pattern_angle",
        native_step=1,
        native_unit_of_measurement=DEGREE,
        native_min_value=-180,
        native_max_value=180,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "toward", value
        ),
        get_fn=lambda coordinator: coordinator.operation_settings.toward,
        available_fn=lambda coordinator: coordinator.operation_settings.channel_mode
        in (STRIPES_PATTERN_VALUE, GRID_PATTERN_VALUE)
        and coordinator.operation_settings.toward_mode
        == PathAngleSetting.absolute_angle.value,
    ),
)

LUBA_WORKING_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="blade_height",
        device_class=NumberDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        native_step=1,
        native_min_value=25,
        native_max_value=70,
        mode=NumberMode.SLIDER,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "blade_height", int(value)
        ),
        set_async_fn=lambda coordinator,
        value: coordinator.async_modify_plan_if_mowing(),
        get_fn=lambda coordinator: coordinator.operation_settings.blade_height,
    ),
)


NUMBER_WORKING_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="working_speed",
        device_class=NumberDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        native_step=0.1,
        native_min_value=0.2,
        native_max_value=0.6,
        set_async_fn=lambda coordinator,
        value: coordinator.async_modify_plan_if_mowing(),
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "speed", value
        ),
    ),
    MammotionConfigNumberEntityDescription(
        key="path_spacing",
        native_step=1,
        device_class=NumberDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        native_min_value=20,
        native_max_value=35,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "channel_width", value
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion number entities."""
    mammotion_devices = entry.runtime_data.mowers

    for mower in mammotion_devices:
        _cleanup_removed_yuka_2_numbers(hass, mower.device.device_name)
        limits: DeviceLimits | None = DeviceConfig().get_working_parameters(
            mower.device.product_key
        )
        if handle := mower.api.get_device_by_name(mower.name):
            limits = handle.device_limits
        entities: list[MammotionConfigNumberEntity] = []

        if is_yuka_2(mower.device.device_name):
            for entity_description in (*YUKA_NUMBER_ENTITIES, *MAP_OFFSET_ENTITIES):
                entities.append(
                    MammotionWorkingNumberEntity(
                        mower.reporting_coordinator, entity_description, None
                    )
                )
            async_add_entities(entities)
            continue

        for entity_description in NUMBER_WORKING_ENTITIES:
            entities.append(
                MammotionWorkingNumberEntity(
                    mower.reporting_coordinator, entity_description, limits
                )
            )

        if DeviceType.is_luba_pro(mower.device.device_name):
            for entity_description in AUDIO_NUMBER_ENTITIES:
                entities.append(
                    MammotionConfigNumberEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )

        for entity_description in MAP_OFFSET_ENTITIES:
            entities.append(
                MammotionConfigNumberEntity(
                    mower.reporting_coordinator, entity_description
                )
            )

        for entity_description in NUMBER_ENTITIES:
            entities.append(
                MammotionConfigNumberEntity(
                    mower.reporting_coordinator, entity_description
                )
            )

        if DeviceType.is_yuka(mower.device.device_name) and not is_yuka_mini_or_ml(
            mower.device.device_name
        ):
            for entity_description in YUKA_NUMBER_ENTITIES:
                entities.append(
                    MammotionConfigNumberEntity(
                        mower.reporting_coordinator, entity_description
                    )
                )
        if not DeviceType.is_yuka(mower.device.device_name):
            for entity_description in LUBA_WORKING_ENTITIES:
                entities.append(
                    MammotionWorkingNumberEntity(
                        mower.reporting_coordinator, entity_description, limits
                    )
                )

        async_add_entities(entities)

    for spino in entry.runtime_data.spino:
        async_add_entities(
            MammotionSpinoNumberEntity(spino.coordinator, entity_description)
            for entity_description in SPINO_NUMBER_ENTITIES
        )


def _cleanup_removed_yuka_2_numbers(hass: HomeAssistant, device_name: str) -> None:
    """Remove number entities that are not exposed by the Yuka app controls."""
    if not is_yuka_2(device_name):
        return
    registry = er.async_get(hass)
    for key in (
        "voice_volume",
        "start_progress",
        "cutting_angle",
        "toward_included_angle",
        "dumping_interval",
    ):
        entity_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{device_name}_{key}"
        )
        if entity_id:
            registry.async_remove(entity_id)


class MammotionConfigNumberEntity(MammotionBaseEntity, RestoreNumber):  # type: ignore[misc]
    """Mammotion config number entity."""

    entity_description: MammotionConfigNumberEntityDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator[Any],
        entity_description: MammotionConfigNumberEntityDescription,
    ) -> None:
        """Initialize the config number entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        if entity_description.native_min_value is not None:
            self._attr_native_min_value = entity_description.native_min_value
            self._attr_native_value = entity_description.native_min_value
        if entity_description.native_max_value is not None:
            self._attr_native_max_value = entity_description.native_max_value
        if entity_description.native_step is not None:
            self._attr_native_step = entity_description.native_step
        if self.entity_description.native_unit_of_measurement == DEGREE:
            self._attr_native_value = 0
        if self.entity_description.key == "toward_included_angle":
            self._attr_native_value = 90
        if self.entity_description.get_fn is not None:
            self._attr_native_value = self.entity_description.get_fn(self.coordinator)
        elif (
            self.entity_description.set_fn is not None
            and self._attr_native_value is not None
        ):
            self.entity_description.set_fn(self.coordinator, self._attr_native_value)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.entity_description.get_fn is not None:
            self._attr_native_value = self.entity_description.get_fn(self.coordinator)
        super()._handle_coordinator_update()

    async def async_set_native_value(self, value: float) -> None:
        """Set native value for number."""
        self._attr_native_value = value
        if self.entity_description.set_fn is not None:
            self.entity_description.set_fn(self.coordinator, value)
        if self.entity_description.set_async_fn is not None:
            await self.entity_description.set_async_fn(self.coordinator, value)
        self.coordinator.async_update_listeners()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last saved value when entity is added to hass."""
        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if (last_number_data is not None) and (
            last_number_data.native_value is not None
        ):
            self._attr_native_value = last_number_data.native_value
            if self.entity_description.set_fn is not None:
                self.entity_description.set_fn(
                    self.coordinator, cast(float, self._attr_native_value)
                )
                self.coordinator.async_update_listeners()

    @property
    def available(self) -> bool:
        """Return True when this number applies to the current Yuka settings."""
        if self.entity_description.available_fn is None:
            return super().available
        return super().available and self.entity_description.available_fn(
            self.coordinator
        )


class MammotionWorkingNumberEntity(MammotionConfigNumberEntity):
    """Mammotion working number entity."""

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator[Any],
        entity_description: MammotionConfigNumberEntityDescription,
        limits: DeviceLimits | None,
    ) -> None:
        """Init MammotionWorkingNumberEntity."""
        super().__init__(coordinator, entity_description)

        if limits is not None and hasattr(limits, entity_description.key):
            self._attr_native_min_value = getattr(limits, entity_description.key).min
            self._attr_native_max_value = getattr(limits, entity_description.key).max
        elif (
            entity_description.native_min_value is not None
            and entity_description.native_max_value is not None
        ):
            self._attr_native_min_value = entity_description.native_min_value
            self._attr_native_max_value = entity_description.native_max_value

        if self.entity_description.get_fn is not None:
            self._attr_native_value = self.entity_description.get_fn(self.coordinator)

        native_val = self._attr_native_value
        native_min = self._attr_native_min_value
        if native_val is not None and native_min is not None:
            self._attr_native_value = max(native_val, native_min)

    @property
    def native_min_value(self) -> float:
        """Return the minimum value."""
        return cast(float, self._attr_native_min_value)

    @property
    def native_max_value(self) -> float:
        """Return the maximum value."""
        return cast(float, self._attr_native_max_value)

    async def async_set_native_value(self, value: float) -> None:
        """Set native value for number and call update_fn if defined."""
        if self._attr_native_value == value:
            return
        self._attr_native_value = value
        if self.entity_description.set_fn is not None:
            self.entity_description.set_fn(self.coordinator, value)
        if self.entity_description.set_async_fn is not None:
            await self.entity_description.set_async_fn(self.coordinator, value)
        self.coordinator.async_update_listeners()
        self.async_write_ha_state()


class MammotionSpinoNumberEntity(MammotionBaseSpinoEntity, NumberEntity):  # type: ignore[misc]
    """Mammotion Spino pool cleaner number entity."""

    entity_description: MammotionSpinoNumberEntityDescription

    def __init__(
        self,
        coordinator: MammotionSpinoCoordinator,
        entity_description: MammotionSpinoNumberEntityDescription,
    ) -> None:
        """Initialize the Spino number entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        """Set a new value."""
        await self.entity_description.set_fn(self.coordinator, value)
        await self.coordinator.async_request_refresh()
