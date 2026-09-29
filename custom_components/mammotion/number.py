"""Number entities for the Mammotion integration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
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
    UnitOfArea,
    UnitOfLength,
    UnitOfSpeed,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.device import PoolCleanerDevice
from pymammotion.data.model.device_info import (
    RECHARGE_LEVEL_RANGE,
    RESUME_LEVEL_RANGE,
    SMART_CHARGE_LEVEL,
)
from pymammotion.data.model.device_limits import DeviceLimits
from pymammotion.utility.device_config import DeviceConfig
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .coordinator import MammotionBaseUpdateCoordinator, MammotionSpinoCoordinator
from .entity import (
    MammotionBaseEntity,
    MammotionBaseSpinoEntity,
    async_add_when_firmware_supports,
)


@dataclass(frozen=True, kw_only=True)
class MammotionConfigNumberEntityDescription(NumberEntityDescription):  # type: ignore[misc]
    """Describes Mammotion number entity."""

    set_fn: Callable[[MammotionBaseUpdateCoordinator[Any], float], None] | None = None
    set_async_fn: (
        Callable[[MammotionBaseUpdateCoordinator[Any], float], Awaitable[None]] | None
    ) = None
    get_fn: Callable[[MammotionBaseUpdateCoordinator[Any]], float | None] | None = None
    # The device's own value, adopted through set_fn while get_fn has none.
    device_fn: Callable[[MammotionBaseUpdateCoordinator[Any]], float | None] | None = (
        None
    )


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoNumberEntityDescription(NumberEntityDescription):  # type: ignore[misc]
    """Describes a Mammotion Spino pool cleaner number entity."""

    value_fn: Callable[[PoolCleanerDevice], float]
    set_fn: Callable[[MammotionSpinoCoordinator, float], Awaitable[None]]


SPINO_NUMBER_ENTITIES: tuple[MammotionSpinoNumberEntityDescription, ...] = (
    MammotionSpinoNumberEntityDescription(
        key="spino_floor_speed",
        device_class=NumberDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        native_min_value=0.1,
        native_max_value=0.2,
        native_step=0.01,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda spino_data: round(spino_data.pool_state.floor_speed, 2),
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

# Same bounds as the app's charge-limit slider; gated on DeviceType.supports_charge_limit.
CHARGE_LIMIT_NUMBER_ENTITY = MammotionConfigNumberEntityDescription(
    key="charge_limit",
    native_min_value=80,
    native_max_value=100,
    native_step=5,
    mode=NumberMode.SLIDER,
    native_unit_of_measurement=PERCENTAGE,
    set_async_fn=lambda coordinator, value: coordinator.async_set_charge_limit(
        int(value)
    ),
    # 0 means the device has not reported its settings yet.
    get_fn=lambda coordinator: (
        coordinator.data.mower_state.charge_settings.charge_limit or None
    ),
)


def _charge_level(level: int, levels: range) -> int | None:
    """Return a recharge/resume level as the app shows it: smart as the slider's top."""
    if level == 0:
        return None
    return levels[-1] if level == SMART_CHARGE_LEVEL else level


# The app's Battery page sliders (step 1); moving one sets a custom level.
CHARGE_LEVEL_NUMBER_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="recharge_level",
        native_min_value=RECHARGE_LEVEL_RANGE[0],
        native_max_value=RECHARGE_LEVEL_RANGE[-1],
        native_step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        set_async_fn=lambda coordinator, value: coordinator.async_set_recharge_level(
            int(value)
        ),
        get_fn=lambda coordinator: _charge_level(
            coordinator.data.mower_state.recharge_level, RECHARGE_LEVEL_RANGE
        ),
    ),
    MammotionConfigNumberEntityDescription(
        key="resume_level",
        native_min_value=RESUME_LEVEL_RANGE[0],
        native_max_value=RESUME_LEVEL_RANGE[-1],
        native_step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        set_async_fn=lambda coordinator, value: coordinator.async_set_resume_level(
            int(value)
        ),
        get_fn=lambda coordinator: _charge_level(
            coordinator.data.mower_state.resume_level, RESUME_LEVEL_RANGE
        ),
    ),
)

# Gated on DeviceType.supports_ride_boundary_distance; the APK names no unit or hard limit.
RIDE_BOUNDARY_DISTANCE_NUMBER_ENTITY = MammotionConfigNumberEntityDescription(
    key="ride_boundary_distance",
    native_min_value=0,
    native_max_value=1,
    native_step=0.1,
    mode=NumberMode.SLIDER,
    # The frontend's step arithmetic can hand over 0.30000000000000004.
    set_fn=lambda coordinator, value: setattr(
        coordinator.operation_settings, "ride_boundary_distance", round(value, 1)
    ),
)

NUMBER_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="start_progress",
        native_min_value=0,
        # The cloud's own schema caps this at 99, not 100.
        native_max_value=99,
        native_step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "start_progress", int(value)
        ),
        set_async_fn=lambda coordinator, value: (
            coordinator.async_change_progress_if_working()
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
        key="dumping_interval",
        native_min_value=5,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "collect_grass_frequency", value
        ),
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
        set_async_fn=lambda coordinator, value: (
            coordinator.async_change_blade_height_if_working()
        ),
        # 0 is OperationSettings' unset default, never a real height.
        get_fn=lambda coordinator: coordinator.operation_settings.blade_height or None,
        device_fn=lambda coordinator: (
            coordinator.data.report_data.work.knife_height or None
        ),
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
        set_async_fn=lambda coordinator, value: (
            coordinator.async_change_speed_if_working()
        ),
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
        limits: DeviceLimits | None = DeviceConfig().get_working_parameters(
            mower.device.product_key
        )
        if handle := mower.api.get_device_by_name(mower.name):
            limits = handle.device_limits
        entities: list[MammotionConfigNumberEntity] = []

        entities.extend(
            MammotionWorkingNumberEntity(
                mower.reporting_coordinator, entity_description, limits
            )
            for entity_description in NUMBER_WORKING_ENTITIES
        )

        if DeviceType.is_luba_pro(mower.device.device_name):
            entities.extend(
                MammotionConfigNumberEntity(
                    mower.reporting_coordinator, entity_description
                )
                for entity_description in AUDIO_NUMBER_ENTITIES
            )

        async_add_when_firmware_supports(
            entry,
            mower.reporting_coordinator,
            supported=partial(
                DeviceType.supports_charge_limit, mower.device.device_name
            ),
            descriptions=(CHARGE_LIMIT_NUMBER_ENTITY, *CHARGE_LEVEL_NUMBER_ENTITIES),
            build=partial(MammotionConfigNumberEntity, mower.reporting_coordinator),
            async_add_entities=async_add_entities,
        )

        if DeviceType.supports_ride_boundary_distance(mower.device.device_name):
            entities.append(
                MammotionConfigNumberEntity(
                    mower.reporting_coordinator, RIDE_BOUNDARY_DISTANCE_NUMBER_ENTITY
                )
            )

        entities.extend(
            MammotionConfigNumberEntity(mower.reporting_coordinator, entity_description)
            for entity_description in MAP_OFFSET_ENTITIES
        )

        entities.extend(
            MammotionConfigNumberEntity(mower.reporting_coordinator, entity_description)
            for entity_description in NUMBER_ENTITIES
        )

        if DeviceType.is_yuka(mower.device.device_name) and not DeviceType.is_yuka_mini(
            mower.device.device_name
        ):
            entities.extend(
                MammotionConfigNumberEntity(
                    mower.reporting_coordinator, entity_description
                )
                for entity_description in YUKA_NUMBER_ENTITIES
            )
        if not DeviceType.is_yuka(mower.device.device_name):
            entities.extend(
                MammotionWorkingNumberEntity(
                    mower.reporting_coordinator, entity_description, limits
                )
                for entity_description in LUBA_WORKING_ENTITIES
            )

        async_add_entities(entities)

    for spino in entry.runtime_data.spino:
        async_add_entities(
            MammotionSpinoNumberEntity(spino.coordinator, entity_description)
            for entity_description in SPINO_NUMBER_ENTITIES
        )


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
            self._attr_native_value = self._current_value()
        elif (
            self.entity_description.set_fn is not None
            and self._attr_native_value is not None
        ):
            self.entity_description.set_fn(self.coordinator, self._attr_native_value)

    def _current_value(self) -> float | None:
        """Return get_fn's value, first adopting device_fn's while it has none."""
        description = self.entity_description
        value = description.get_fn(self.coordinator) if description.get_fn else None
        if (
            value is None
            and description.device_fn is not None
            and description.set_fn is not None
            and (value := description.device_fn(self.coordinator)) is not None
        ):
            description.set_fn(self.coordinator, value)
        return value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.entity_description.get_fn is not None:
            self._attr_native_value = self._current_value()
        super()._handle_coordinator_update()

    async def async_set_native_value(self, value: float) -> None:
        """Set native value for number."""
        self._attr_native_value = value
        if self.entity_description.set_fn is not None:
            self.entity_description.set_fn(self.coordinator, value)
        if self.entity_description.set_async_fn is not None:
            await self.entity_description.set_async_fn(self.coordinator, value)
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
        if self.entity_description.device_fn is not None:
            self._attr_native_value = self._current_value()


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
            self._attr_native_value = self._current_value()

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
