"""Support for Mammotion switches."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from pymammotion.data.model.hash_list import AreaHashNameList
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .const import DOMAIN
from .coordinator import (
    MammotionBaseUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)
from .entity import MammotionBaseEntity


@dataclass(frozen=True, kw_only=True)
class MammotionSwitchEntityDescription(SwitchEntityDescription):
    """Describes Mammotion switch entity."""

    key: str


@dataclass(frozen=True, kw_only=True)
class MammotionAsyncSwitchEntityDescription(MammotionSwitchEntityDescription):
    """Describes Mammotion switch entity."""

    polling: bool = False
    poll_func: Callable[[MammotionBaseUpdateCoordinator], Awaitable[None]] = None
    is_on_func: Callable[[MammotionBaseUpdateCoordinator], bool] = None
    set_fn: Callable[[MammotionBaseUpdateCoordinator, bool], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class MammotionConfigSwitchEntityDescription(MammotionSwitchEntityDescription):
    """Describes Mammotion Config switch entity."""

    set_fn: Callable[[MammotionBaseUpdateCoordinator, bool], None]


@dataclass(frozen=True, kw_only=True)
class MammotionConfigAreaSwitchEntityDescription(MammotionSwitchEntityDescription):
    """Describes the Areas entities."""

    area: int
    set_fn: Callable[[MammotionBaseUpdateCoordinator, bool, int], None]


YUKA_CONFIG_SWITCH_ENTITIES: tuple[MammotionConfigSwitchEntityDescription, ...] = (
    MammotionConfigSwitchEntityDescription(
        key="is_mow",
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "is_mow", value
        ),
    ),
    MammotionConfigSwitchEntityDescription(
        key="is_dump",
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "is_dump", value
        ),
    ),
    MammotionConfigSwitchEntityDescription(
        key="is_edge",
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "is_edge", value
        ),
    ),
)

MINI_AND_X_SERIES_CONFIG_SWITCH_ENTITIES: tuple[
    MammotionAsyncSwitchEntityDescription, ...
] = (
    MammotionAsyncSwitchEntityDescription(
        key="manual_light",
        is_on_func=lambda coordinator: coordinator.data.mower_state.lamp_info.manual_light,
        set_fn=lambda coordinator, value: coordinator.async_set_manual_light(value),
    ),
    MammotionAsyncSwitchEntityDescription(
        key="night_light",
        is_on_func=lambda coordinator: coordinator.data.mower_state.lamp_info.night_light,
        set_fn=lambda coordinator, value: coordinator.async_set_night_light(value),
    ),
)

SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="side_led",
        polling=True,
        poll_func=lambda coordinator: coordinator.async_read_sidelight(),
        is_on_func=lambda coordinator: bool(
            coordinator.data.mower_state.side_led.operate
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_sidelight(int(value)),
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionAsyncSwitchEntityDescription(
        key="rain_detection",
        polling=True,
        poll_func=lambda coordinator: coordinator.async_read_rain_detection(),
        is_on_func=lambda coordinator: coordinator.data.mower_state.rain_detection,
        set_fn=lambda coordinator, value: coordinator.async_set_rain_detection(value),
        entity_category=EntityCategory.CONFIG,
    ),
)

LUBA_1_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="blade_status",
        set_fn=lambda coordinator, value: coordinator.async_start_stop_blades(value),
        is_on_func=lambda coordinator: coordinator.data.mower_state.blade_status,
    ),
)

UPDATE_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="schedule_updates",
        is_on_func=lambda coordinator: coordinator.data.enabled,
        set_fn=lambda coordinator, value: coordinator.set_scheduled_updates(value),
    ),
)

CONFIG_SWITCH_ENTITIES: tuple[MammotionConfigSwitchEntityDescription, ...] = (
    MammotionConfigSwitchEntityDescription(
        key="rain_tactics",
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "rain_tactics", int(value)
        ),
    ),
)


# Example setup usage
async def async_setup_entry(
    hass: HomeAssistant, entry: MammotionConfigEntry, async_add_entities: Callable
) -> None:
    """Set up the Mammotion switch entities."""
    mammotion_devices = entry.runtime_data.mowers

    for mower in mammotion_devices:
        added_areas: set[int] = set()
        # TODO create maps coordinator
        coordinator = mower.reporting_coordinator

        update_areas = partial(
            async_add_area_entities,
            coordinator,
            added_areas,
            async_add_entities,
        )

        update_areas()
        coordinator.async_add_listener(update_areas)

        entities = []
        for entity_description in SWITCH_ENTITIES:
            entity = MammotionSwitchEntity(coordinator, entity_description)
            entities.append(entity)

        for entity_description in CONFIG_SWITCH_ENTITIES:
            config_entity = MammotionConfigSwitchEntity(coordinator, entity_description)
            entities.append(config_entity)

        for entity_description in UPDATE_SWITCH_ENTITIES:
            config_entity = MammotionUpdateSwitchEntity(coordinator, entity_description)
            entities.append(config_entity)

        if DeviceType.is_yuka(mower.device.device_name) and not DeviceType.is_yuka_mini(
            mower.device.device_name
        ):
            for entity_description in YUKA_CONFIG_SWITCH_ENTITIES:
                config_entity = MammotionConfigSwitchEntity(
                    coordinator, entity_description
                )
                entities.append(config_entity)
        if DeviceType.is_luba1(mower.device.device_name):
            for entity_description in LUBA_1_SWITCH_ENTITIES:
                entity = MammotionSwitchEntity(coordinator, entity_description)
                entities.append(entity)

        if DeviceType.is_mini_or_x_series(mower.device.device_name):
            for entity_description in MINI_AND_X_SERIES_CONFIG_SWITCH_ENTITIES:
                config_entity = MammotionSwitchEntity(coordinator, entity_description)
                entities.append(config_entity)
        async_add_entities(entities)


class MammotionSwitchEntity(MammotionBaseEntity, SwitchEntity, RestoreEntity):
    """Mammotion switch entity."""

    entity_description: MammotionAsyncSwitchEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionAsyncSwitchEntityDescription,
    ) -> None:
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        if callable(entity_description.is_on_func):
            self._attr_is_on = entity_description.is_on_func(self.coordinator)
        else:
            self._attr_is_on = False  # Default state

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        await self.entity_description.set_fn(self.coordinator, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        await self.entity_description.set_fn(self.coordinator, False)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update the entity state."""
        if (
            self.entity_description.polling
            and self.entity_description.poll_func is not None
        ):
            await self.entity_description.poll_func(self.coordinator)

        if self.entity_description.is_on_func is not None:
            self._attr_is_on = self.entity_description.is_on_func(self.coordinator)
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        if not (last_state := await self.async_get_last_state()):
            return
        self._attr_is_on = last_state.state == STATE_ON


class MammotionUpdateSwitchEntity(MammotionBaseEntity, SwitchEntity, RestoreEntity):
    entity_description: MammotionAsyncSwitchEntityDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionAsyncSwitchEntityDescription,
    ) -> None:
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_is_on = True  # Default state

    @property
    def is_on(self) -> bool:
        """Return if settings is on or off."""
        if self.entity_description.is_on_func is not None:
            return self.entity_description.is_on_func(self.coordinator)
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        await self.entity_description.set_fn(self.coordinator, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        await self.entity_description.set_fn(self.coordinator, False)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update the entity state."""
        if self.entity_description.is_on_func is not None:
            self._attr_is_on = self.entity_description.is_on_func(self.coordinator)
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        if not (last_state := await self.async_get_last_state()):
            return
        self._attr_is_on = last_state.state == STATE_ON


class MammotionConfigSwitchEntity(MammotionBaseEntity, SwitchEntity, RestoreEntity):
    entity_description: MammotionConfigSwitchEntityDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionConfigSwitchEntityDescription,
    ) -> None:
        """Initialize the config switch entities."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def is_on(self) -> bool:
        """Return if settings is on or off."""
        return getattr(
            self.coordinator.operation_settings, self.entity_description.key, False
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        self._attr_is_on = True
        self.entity_description.set_fn(self.coordinator, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        self._attr_is_on = False
        self.entity_description.set_fn(self.coordinator, False)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update the entity state."""


class MammotionConfigAreaSwitchEntity(MammotionBaseEntity, SwitchEntity, RestoreEntity):
    """Mammotion Config Area Switch Entity."""

    entity_description: MammotionConfigAreaSwitchEntityDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionConfigAreaSwitchEntityDescription,
    ) -> None:
        """Initialize the area switch entity."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_extra_state_attributes = {"hash": entity_description.area}
        self._attr_is_on = (
            entity_description.area in self.coordinator.operation_settings.areas
        )  # Default state

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        self._attr_is_on = True
        self.entity_description.set_fn(
            self.coordinator,
            True,
            self.entity_description.area,
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        self._attr_is_on = False
        self.entity_description.set_fn(
            self.coordinator,
            False,
            self.entity_description.area,
        )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        last_state = await self.async_get_last_state()
        if last_state and last_state.state == STATE_ON:
            await self.async_turn_on()


@callback
def async_add_area_entities(
    coordinator: MammotionReportUpdateCoordinator,
    added_areas: set[int],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Handle addition of mowing areas."""

    if coordinator.data is None:
        return

    switch_entities: list[MammotionConfigAreaSwitchEntity] = []
    areas = list(map(int, coordinator.data.map.area.keys()))
    area_name_hashes = [area.hash for area in coordinator.data.map.area_name]
    area_name = coordinator.data.map.area_name
    new_areas = (set(areas) | set(area_name_hashes)) - added_areas
    if new_areas:
        for area_id in new_areas:
            existing_name: AreaHashNameList | None = next(
                (area for area in area_name if str(area.hash) == str(area_id)),
                None,
            )
            name = (
                existing_name.name
                if (existing_name and existing_name.name)
                else f"{area_id}"
            )

            def set_area_entity(coord, bool_val, value):
                if bool_val:
                    coord.operation_settings.areas.add(value)
                elif value in coord.operation_settings.areas:
                    coord.operation_settings.areas.remove(value)

            base_area_switch_entity = MammotionConfigAreaSwitchEntityDescription(
                key=f"{area_id}",
                translation_key="area",
                translation_placeholders={"name": name},
                area=area_id,
                name=f"{name}",
                set_fn=set_area_entity,
            )
            switch_entities.append(
                MammotionConfigAreaSwitchEntity(
                    coordinator,
                    base_area_switch_entity,
                )
            )
            added_areas.add(area_id)

    old_areas = set(areas) - added_areas
    if old_areas:
        async_remove_entities(coordinator, old_areas)
        for area in old_areas:
            added_areas.remove(area)
    if switch_entities:
        async_add_entities(switch_entities)


def async_remove_entities(
    coordinator: MammotionBaseUpdateCoordinator,
    old_areas: set[int],
) -> None:
    """Remove area switch sensors from Home Assistant."""
    registry = er.async_get(coordinator.hass)
    for area in old_areas:
        entity_id = registry.async_get_entity_id(
            SWITCH_DOMAIN, DOMAIN, f"{coordinator.device_name}_{area}"
        )
        if entity_id:
            registry.async_remove(entity_id)
