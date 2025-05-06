from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.device_limits import DeviceLimits
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .coordinator import MammotionBaseUpdateCoordinator
from .entity import MammotionBaseEntity


@dataclass(frozen=True, kw_only=True)
class MammotionConfigNumberEntityDescription(NumberEntityDescription):
    """Describes Mammotion number entity."""

    set_fn: Callable[[MammotionBaseUpdateCoordinator, float], None]
    get_fn: Callable[[MammotionBaseUpdateCoordinator], float | None] = None
    update_fn: Callable[
        [MammotionBaseUpdateCoordinator, float], None | Awaitable[None]
    ] = None


NUMBER_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="start_progress",
        min_value=0,
        max_value=100,
        step=1,
        mode=NumberMode.SLIDER,
        native_unit_of_measurement=PERCENTAGE,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "start_progress", value
        ),
    ),
    MammotionConfigNumberEntityDescription(
        key="cutting_angle",
        step=1,
        native_unit_of_measurement=DEGREE,
        min_value=-180,
        max_value=180,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "toward", value
        ),
    ),
    MammotionConfigNumberEntityDescription(
        key="toward_included_angle",
        step=1,
        native_unit_of_measurement=DEGREE,
        min_value=-180,
        max_value=180,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "toward_included_angle", value
        ),
    ),
)

YUKA_NUMBER_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="dumping_interval",
        min_value=5,
        max_value=100,
        step=1,
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
        step=1,
        min_value=25,
        max_value=70,
        mode=NumberMode.BOX,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "blade_height", int(value)
        ),
        get_fn=lambda coordinator: coordinator.data.report_data.work.knife_height,
        update_fn=lambda coordinator, value: coordinator.async_blade_height(int(value)),
    ),
)


NUMBER_WORKING_ENTITIES: tuple[MammotionConfigNumberEntityDescription, ...] = (
    MammotionConfigNumberEntityDescription(
        key="working_speed",
        device_class=NumberDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        step=0.1,
        min_value=0.2,
        max_value=0.6,
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "speed", value
        ),
    ),
    MammotionConfigNumberEntityDescription(
        key="path_spacing",
        step=1,
        device_class=NumberDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        min_value=20,
        max_value=35,
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
    mammotion_devices = entry.runtime_data

    for mower in mammotion_devices:
        limits = mower.device_limits

        entities: list[MammotionConfigNumberEntity] = []

        for entity_description in NUMBER_WORKING_ENTITIES:
            entity = MammotionWorkingNumberEntity(
                mower.reporting_coordinator, entity_description, limits
            )
            entities.append(entity)

        for entity_description in NUMBER_ENTITIES:
            entity = MammotionConfigNumberEntity(
                mower.reporting_coordinator, entity_description
            )
            entities.append(entity)

        if DeviceType.is_yuka(mower.device.deviceName):
            for entity_description in YUKA_NUMBER_ENTITIES:
                entity = MammotionConfigNumberEntity(
                    mower.reporting_coordinator, entity_description
                )
                entities.append(entity)
        else:
            for entity_description in LUBA_WORKING_ENTITIES:
                entity = MammotionWorkingNumberEntity(
                    mower.reporting_coordinator, entity_description, limits
                )
                entities.append(entity)

        async_add_entities(entities)


class MammotionConfigNumberEntity(MammotionBaseEntity, RestoreNumber):
    entity_description: MammotionConfigNumberEntityDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionConfigNumberEntityDescription,
    ) -> None:
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_native_min_value = entity_description.min_value
        self._attr_native_max_value = entity_description.max_value
        self._attr_native_step = entity_description.step
        self._attr_native_value = self._attr_native_min_value  # Default value
        if self.entity_description.native_unit_of_measurement == DEGREE:
            self._attr_native_value = 0
        if self.entity_description.key == "toward_included_angle":
            self._attr_native_value = 90
        self.entity_description.set_fn(self.coordinator, self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Set native value for number and call update_fn if defined."""
        self._attr_native_value = value
        self.entity_description.set_fn(self.coordinator, value)
        
        if self.entity_description.update_fn is not None:
            result = self.entity_description.update_fn(self.coordinator, value)
            if result is not None and hasattr(result, "__await__"):
                await result
        # If this is blade height and no update_fn is defined, directly set it on the device
        elif self.entity_description.key == "blade_height":
            await self.coordinator.async_blade_height(int(value))
            # Request an update to refresh the UI with the actual value
            await self.coordinator.async_request_iot_sync()
        
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if (last_number_data is not None) and (
            last_number_data.native_value is not None
        ):
            await self.async_set_native_value(last_number_data.native_value)


class MammotionWorkingNumberEntity(MammotionConfigNumberEntity):
    """Mammotion working number entity."""

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionConfigNumberEntityDescription,
        limits: DeviceLimits,
    ) -> None:
        """Init MammotionWorkingNumberEntity."""
        super().__init__(coordinator, entity_description)

        if hasattr(limits, entity_description.key):
            self._attr_native_min_value = getattr(limits, entity_description.key).min
            self._attr_native_max_value = getattr(limits, entity_description.key).max
        else:
            # Fallback to the values from entity_description
            self._attr_native_min_value = entity_description.min_value
            self._attr_native_max_value = entity_description.max_value

        if self.entity_description.get_fn is not None:
            self._attr_native_value = self.entity_description.get_fn(self.coordinator)

        if self._attr_native_value < self._attr_native_min_value:
            self._attr_native_value = self._attr_native_min_value

        self.entity_description.set_fn(self.coordinator, self._attr_native_value)

    @property
    def native_min_value(self) -> float:
        """Return the minimum value."""
        return self._attr_native_min_value

    @property
    def native_max_value(self) -> float:
        """Return the maximum value."""
        return self._attr_native_max_value

    @property
    def native_value(self) -> float | None:
        """Return the current value if get_fn is defined."""
        if self.entity_description.get_fn is not None:
            value = self.entity_description.get_fn(self.coordinator)
            # If value is within range, update the operation settings too
            if value is not None and self._attr_native_min_value <= value <= self._attr_native_max_value:
                self.entity_description.set_fn(self.coordinator, value)
                return value
        return self._attr_native_value
