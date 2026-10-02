"""Support for Mammotion switches."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from functools import partial
from typing import Any

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from pymammotion.data.model.device import PoolCleanerDevice
from pymammotion.data.model.device_info import (
    RECHARGE_LEVEL_RANGE,
    RESUME_LEVEL_RANGE,
    SMART_CHARGE_LEVEL,
)
from pymammotion.data.model.enums import CollectorState, DumpState
from pymammotion.data.model.pool_state import SpinoToggle
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .const import DOMAIN, LOGGER
from .coordinator import (
    REMOTE_DRIVE_LIVE_PHASES,
    MammotionBaseUpdateCoordinator,
    MammotionReportUpdateCoordinator,
    MammotionSpinoCoordinator,
)
from .entity import (
    MammotionBaseEntity,
    MammotionBaseSpinoEntity,
    async_add_when_firmware_supports,
    async_add_when_supported,
    invalidate_cached_name,
    name_starts_with_prefix,
    strip_prefix_word,
    supports_grass_collection,
)

# Matches pymammotion's auto-generated fallback names ("area 1", "area 2", …).
# These carry no user intent and must be treated the same as empty names.
_PYMAMMOTION_AUTO_NAME = re.compile(r"^area\s+\d+$", re.IGNORECASE)


def _area_translation_key(hass: HomeAssistant, name: str) -> str:
    """Prefix "Area " for grouping, unless the name already starts with it."""
    if name_starts_with_prefix(hass, SWITCH_DOMAIN, "area", name):
        return "area_plain"
    return "area"


def _area_unique_id(coordinator: MammotionBaseUpdateCoordinator[Any], area: int) -> str:
    """Registry unique_id for an area switch, matching MammotionBaseEntity."""
    return f"{coordinator.unique_name}_{area}"


def _async_rekey_area_unique_id(
    registry: er.EntityRegistry, entity_id: str, new_unique_id: str
) -> bool:
    """Re-key a registry entry to a new unique_id; no-op when the id is taken."""
    if registry.async_get_entity_id(SWITCH_DOMAIN, DOMAIN, new_unique_id) is not None:
        return False
    registry.async_update_entity(entity_id, new_unique_id=new_unique_id)
    return True


def _stale_area_registry_entries(
    registry: er.EntityRegistry,
    coordinator: MammotionReportUpdateCoordinator,
    known_hashes: set[int],
) -> list[er.RegistryEntry]:
    """Return the device's area-switch registry entries left from a previous session.

    A leftover is an entry whose hash is neither reported by the device nor
    tracked in-memory — after an integration reload or HA restart these must be
    re-keyed to the device's new hashes instead of duplicate "_2" entities
    being minted.
    """
    prefix = f"{coordinator.unique_name}_"
    stale = []
    for reg_entry in list(registry.entities.values()):
        if (
            reg_entry.domain != SWITCH_DOMAIN
            or reg_entry.platform != DOMAIN
            or reg_entry.translation_key not in ("area", "area_plain")
            or not reg_entry.unique_id.startswith(prefix)
        ):
            continue
        suffix = reg_entry.unique_id.removeprefix(prefix)
        if suffix.lstrip("-").isdigit() and int(suffix) not in known_hashes:
            stale.append(reg_entry)
    return stale


def _async_rekey_stale_entry_for_area(
    registry: er.EntityRegistry,
    stale_entries: list[er.RegistryEntry],
    coordinator: MammotionReportUpdateCoordinator,
    area_id: int,
    area_name: str,
) -> None:
    """Re-key a stale registry entry matching the area's name, if one exists.

    original_name is the translated "Area {name}" (or the bare name when it
    already starts with "Area"), so match on the suffix.
    """
    for reg_entry in stale_entries:
        reg_name = reg_entry.original_name
        if reg_name and (reg_name == area_name or reg_name.endswith(f" {area_name}")):
            _async_rekey_area_unique_id(
                registry, reg_entry.entity_id, _area_unique_id(coordinator, area_id)
            )
            stale_entries.remove(reg_entry)
            return


@dataclass(frozen=True, kw_only=True)
class MammotionSwitchEntityDescription(SwitchEntityDescription):
    """Describes Mammotion switch entity."""

    key: str


@dataclass(frozen=True, kw_only=True)
class MammotionAsyncSwitchEntityDescription(MammotionSwitchEntityDescription):
    """Describes Mammotion switch entity."""

    is_on_func: Callable[[MammotionBaseUpdateCoordinator[Any]], bool | None] | None = (
        None
    )
    set_fn: Callable[[MammotionBaseUpdateCoordinator[Any], bool], Awaitable[None]]
    available_fn: Callable[[MammotionBaseUpdateCoordinator[Any]], bool] | None = None
    #: For switches that restore a transport: gating them on one would strand them.
    available_without_transport: bool = False


@dataclass(frozen=True, kw_only=True)
class MammotionConfigSwitchEntityDescription(MammotionSwitchEntityDescription):
    """Describes Mammotion Config switch entity."""

    set_fn: Callable[[MammotionBaseUpdateCoordinator[Any], bool], None]


@dataclass(frozen=True, kw_only=True)
class MammotionConfigAreaSwitchEntityDescription(MammotionSwitchEntityDescription):
    """Describes the Areas entities."""

    area: int
    set_fn: Callable[[MammotionBaseUpdateCoordinator[Any], bool, int], None]


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoSwitchEntityDescription(SwitchEntityDescription):
    """Describes a Mammotion Spino pool cleaner switch entity."""

    key: str
    is_on_fn: Callable[[PoolCleanerDevice], bool]
    set_fn: Callable[[MammotionSpinoCoordinator, bool], Awaitable[None]]


SPINO_SWITCH_ENTITIES: tuple[MammotionSpinoSwitchEntityDescription, ...] = (
    MammotionSpinoSwitchEntityDescription(
        key="spino_buzzer",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda spino_data: spino_data.pool_state.buzzer,
        set_fn=lambda coordinator, value: coordinator.async_set_pool_toggle(
            SpinoToggle.buzzer, value
        ),
    ),
    MammotionSpinoSwitchEntityDescription(
        key="spino_turbo_clean",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda spino_data: spino_data.pool_state.turbo_clean,
        set_fn=lambda coordinator, value: coordinator.async_set_pool_toggle(
            SpinoToggle.turbo_clean, value
        ),
    ),
    MammotionSpinoSwitchEntityDescription(
        key="spino_platform_cleaning",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda spino_data: spino_data.pool_state.platform_cleaning,
        set_fn=lambda coordinator, value: coordinator.async_set_pool_toggle(
            SpinoToggle.platform_cleaning, value
        ),
    ),
    MammotionSpinoSwitchEntityDescription(
        key="spino_waterline_parking",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda spino_data: spino_data.pool_state.waterline_parking,
        set_fn=lambda coordinator, value: coordinator.async_set_pool_toggle(
            SpinoToggle.waterline_parking, value
        ),
    ),
)


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

# Manual sweep/dump, the two toggles the app puts on its manual-control page.
# Both stay unavailable while the mower reports no collector fitted, matching the
# app hiding them outright on collector_installation_status == 0.
GRASS_COLLECTION_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="manual_grass_collection",
        is_on_func=lambda coordinator: (
            coordinator.grass_collection_state is CollectorState.COLLECTING
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_grass_collection(value),
        available_fn=lambda coordinator: coordinator.grass_collector_installed,
    ),
    MammotionAsyncSwitchEntityDescription(
        key="manual_grass_dump",
        # Raised and pouring both mean "not stowed".
        is_on_func=lambda coordinator: (
            coordinator.grass_dump_state in (DumpState.RAISED, DumpState.POURING)
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_grass_dump(value),
        available_fn=lambda coordinator: coordinator.grass_collector_installed,
    ),
)

FILL_LIGHT_CONFIG_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="manual_light",
        is_on_func=lambda coordinator: (
            coordinator.data.mower_state.lamp_info.manual_light
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_manual_light(value),
    ),
    MammotionAsyncSwitchEntityDescription(
        key="night_light",
        is_on_func=lambda coordinator: (
            coordinator.data.mower_state.lamp_info.night_light
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_night_light(value),
    ),
)

AUDIO_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="voice_on_off",
        is_on_func=lambda coordinator: coordinator.data.mower_state.audio.volume > 0,
        set_fn=lambda coordinator, value: coordinator.async_set_voice_on_off(value),
        entity_category=EntityCategory.CONFIG,
    ),
)

SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="side_led",
        is_on_func=lambda coordinator: (
            coordinator.data.mower_state.side_led.enable == 0
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_sidelight(int(value)),
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionAsyncSwitchEntityDescription(
        key="rain_detection",
        is_on_func=lambda coordinator: coordinator.data.mower_state.rain_detection,
        set_fn=lambda coordinator, value: coordinator.async_set_rain_detection(value),
        entity_category=EntityCategory.CONFIG,
    ),
)


def _smart_level(level: int) -> bool | None:
    """Return whether a recharge/resume level is smart; None while unread (0)."""
    return None if level == 0 else level == SMART_CHARGE_LEVEL


# Gated per device on DeviceType.supports_charge_limit (pool robots and old firmware excluded).
CHARGE_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="smart_charge",
        is_on_func=lambda coordinator: (
            settings.smart_charge
            if (settings := coordinator.data.mower_state.charge_settings).reported
            else None
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_smart_charge(value),
        entity_category=EntityCategory.CONFIG,
    ),
    # Leaving smart, the app writes the top of the level's slider.
    MammotionAsyncSwitchEntityDescription(
        key="smart_recharge_level",
        is_on_func=lambda coordinator: _smart_level(
            coordinator.data.mower_state.recharge_level
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_recharge_level(
            SMART_CHARGE_LEVEL if value else RECHARGE_LEVEL_RANGE[-1]
        ),
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionAsyncSwitchEntityDescription(
        key="smart_resume_level",
        is_on_func=lambda coordinator: _smart_level(
            coordinator.data.mower_state.resume_level
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_resume_level(
            SMART_CHARGE_LEVEL if value else RESUME_LEVEL_RANGE[-1]
        ),
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


async def _async_set_scheduled_updates(
    coordinator: MammotionBaseUpdateCoordinator[Any], value: bool
) -> None:
    """Adapt the coordinator's setter, which reports whether the position changed."""
    await coordinator.set_scheduled_updates(value)


UPDATE_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="schedule_updates",
        is_on_func=lambda coordinator: coordinator.data.enabled,
        set_fn=_async_set_scheduled_updates,
    ),
)

BLUETOOTH_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="bluetooth_enabled",
        is_on_func=lambda coordinator: coordinator.bluetooth_enabled,
        set_fn=lambda coordinator, value: coordinator.async_set_bluetooth_enabled(
            value
        ),
        available_without_transport=True,
        entity_category=EntityCategory.CONFIG,
    ),
)

CLOUD_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="cloud_enabled",
        is_on_func=lambda coordinator: coordinator.cloud_enabled,
        set_fn=lambda coordinator, value: coordinator.async_set_cloud_enabled(value),
        available_without_transport=True,
        entity_category=EntityCategory.CONFIG,
    ),
)

#: Starts the cloud remote-drive session; its state is the session's, never restored.
REMOTE_DRIVE_SWITCH = SwitchEntityDescription(key="remote_drive")

AUTO_CHANGE_DIRECTION_CONFIG_SWITCH_ENTITIES: tuple[
    MammotionConfigSwitchEntityDescription, ...
] = (
    MammotionConfigSwitchEntityDescription(
        key="auto_change_direction",
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, "auto_change_direction", int(value)
        ),
    ),
)


def _grass_collection_entities(
    coordinator: MammotionBaseUpdateCoordinator[Any], device_name: str
) -> list[MammotionSwitchEntity]:
    """Manual sweep and dump toggles, for mowers that take a grass collector."""
    if not supports_grass_collection(device_name):
        return []
    return [
        MammotionSwitchEntity(coordinator, description)
        for description in GRASS_COLLECTION_SWITCH_ENTITIES
    ]


def _spino_switch_supported(
    coordinator: MammotionSpinoCoordinator,
    description: MammotionSpinoSwitchEntityDescription,
) -> bool:
    """Whether this pool cleaner has the hardware behind *description*.

    Only the force/turbo module is model-dependent: the app hides that row on
    the S1 and the SP (``SwimmingPoolTestToolsActivity:251-256``) and shows the
    rest on every cleaner.
    """
    if description.key != "spino_turbo_clean":
        return True
    return DeviceType.value_of_str(
        coordinator.device_name, coordinator.device.product_key
    ) not in (DeviceType.SWIMMINGPOOL_S1, DeviceType.SWIMMINGPOOL_SP)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion switch entities."""
    mammotion_devices = entry.runtime_data.mowers

    for mower in mammotion_devices:
        added_areas: set[int] = set()
        area_entities_by_name: dict[str, MammotionConfigAreaSwitchEntity] = {}
        coordinator = mower.reporting_coordinator

        update_areas = partial(
            async_add_area_entities,
            coordinator,
            added_areas,
            area_entities_by_name,
            async_add_entities,
        )

        update_areas()
        coordinator.subscribe_map_updated(update_areas)

        device_name = mower.device.device_name
        entities: list[SwitchEntity] = [
            MammotionSwitchEntity(coordinator, d) for d in SWITCH_ENTITIES
        ]

        if DeviceType.is_luba_pro(device_name):
            entities.extend(
                MammotionSwitchEntity(coordinator, d) for d in AUDIO_SWITCH_ENTITIES
            )

        async_add_when_firmware_supports(
            entry,
            coordinator,
            supported=partial(DeviceType.supports_charge_limit, device_name),
            descriptions=CHARGE_SWITCH_ENTITIES,
            build=partial(MammotionSwitchEntity, coordinator),
            async_add_entities=async_add_entities,
        )

        async_add_when_firmware_supports(
            entry,
            coordinator,
            supported=partial(DeviceType.supports_auto_change_direction, device_name),
            descriptions=AUTO_CHANGE_DIRECTION_CONFIG_SWITCH_ENTITIES,
            build=partial(MammotionConfigSwitchEntity, coordinator),
            async_add_entities=async_add_entities,
        )
        entities.extend(
            MammotionUpdateSwitchEntity(coordinator, d) for d in UPDATE_SWITCH_ENTITIES
        )
        entities.extend(
            MammotionSwitchEntity(coordinator, d) for d in BLUETOOTH_SWITCH_ENTITIES
        )
        async_add_when_supported(
            entry,
            coordinator,
            supported=coordinator.supports_remote_drive,
            descriptions=(REMOTE_DRIVE_SWITCH,),
            build=partial(MammotionRemoteDriveSwitchEntity, coordinator),
            async_add_entities=async_add_entities,
        )
        # A mower without a cloud identity (BLE-only) has no cloud to switch.
        if mower.device.iot_id:
            entities.extend(
                MammotionSwitchEntity(coordinator, d) for d in CLOUD_SWITCH_ENTITIES
            )

        if DeviceType.is_yuka(device_name) and not DeviceType.is_yuka_mini(device_name):
            entities.extend(
                MammotionConfigSwitchEntity(coordinator, d)
                for d in YUKA_CONFIG_SWITCH_ENTITIES
            )

        entities.extend(_grass_collection_entities(coordinator, device_name))

        if DeviceType.is_luba1(device_name):
            entities.extend(
                MammotionSwitchEntity(coordinator, d) for d in LUBA_1_SWITCH_ENTITIES
            )

        if DeviceType.is_support_fill_light(device_name):
            # The app hides the night-light row on Yuka MV while keeping the manual
            # light (CarSettingDrawerFragment: `!isSupportFillLight() || isYukaMV()`).
            entities.extend(
                MammotionSwitchEntity(coordinator, d)
                for d in FILL_LIGHT_CONFIG_SWITCH_ENTITIES
                if not (
                    d.key == "night_light"
                    and DeviceType.value_of_str(device_name).is_yuka_mv()
                )
            )

        async_add_entities(entities)

    for spino in entry.runtime_data.spino:
        async_add_entities(
            MammotionSpinoSwitchEntity(spino.coordinator, entity_description)
            for entity_description in SPINO_SWITCH_ENTITIES
            if _spino_switch_supported(spino.coordinator, entity_description)
        )


class MammotionSwitchEntity(MammotionBaseEntity, SwitchEntity, RestoreEntity):
    """Mammotion switch entity."""

    entity_description: MammotionAsyncSwitchEntityDescription
    _attr_has_entity_name = True
    _sets_in_flight = 0

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator[Any],
        entity_description: MammotionAsyncSwitchEntityDescription,
    ) -> None:
        """Initialize the switch entity."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        if callable(entity_description.is_on_func):
            self._attr_is_on = entity_description.is_on_func(self.coordinator)
        else:
            self._attr_is_on = False  # Default state

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if self.entity_description.available_without_transport:
            return self.coordinator.data is not None
        if self.entity_description.available_fn is not None:
            return super().available and self.entity_description.available_fn(
                self.coordinator
            )
        return super().available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        """Show *value* at once, reverting it if the device refuses."""
        self._attr_is_on = value
        self.async_write_ha_state()
        self._sets_in_flight += 1
        try:
            await self.entity_description.set_fn(self.coordinator, value)
        except Exception:
            self._attr_is_on = not value
            self.async_write_ha_state()
            raise
        finally:
            self._sets_in_flight -= 1

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Until the device answers a press, a push still carries the old value.
        if callable(self.entity_description.is_on_func) and not self._sets_in_flight:
            self._attr_is_on = self.entity_description.is_on_func(self.coordinator)
        super()._handle_coordinator_update()

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


class MammotionRemoteDriveSwitchEntity(MammotionBaseEntity, SwitchEntity):
    """Starts and stops the mower's cloud remote-drive session."""

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator[Any],
        entity_description: SwitchEntityDescription,
    ) -> None:
        """Initialize the remote-drive switch."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def is_on(self) -> bool:
        """On while the session holds, or is requesting, the drive token."""
        return self.coordinator.remote_drive_phase in REMOTE_DRIVE_LIVE_PHASES

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Request the token; the confirm button then accepts the safety notice."""
        await self.coordinator.async_start_remote_drive()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the mower and release the token."""
        await self.coordinator.async_stop_remote_drive()


class MammotionUpdateSwitchEntity(MammotionBaseEntity, SwitchEntity, RestoreEntity):
    """Mammotion switch entity for controlling scheduled updates."""

    entity_description: MammotionAsyncSwitchEntityDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator[Any],
        entity_description: MammotionAsyncSwitchEntityDescription,
    ) -> None:
        """Initialize the update switch entity."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_is_on = True  # Default state

    @property
    def available(self) -> bool:
        """Return True whenever there is state to act on.

        Deliberately not the transport-based check the other entities use: this
        switch is the only way back from updates-off, so it must never strand
        itself behind an offline device (issue #889).
        """
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool | None:
        """Return if settings is on or off."""
        if self.entity_description.is_on_func is not None:
            return self.entity_description.is_on_func(self.coordinator)
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self.entity_description.set_fn(self.coordinator, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self.entity_description.set_fn(self.coordinator, False)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update the entity state."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        if not (last_state := await self.async_get_last_state()):
            return
        self._attr_is_on = last_state.state == STATE_ON


class MammotionConfigSwitchEntity(MammotionBaseEntity, SwitchEntity, RestoreEntity):
    """Mammotion config switch entity."""

    entity_description: MammotionConfigSwitchEntityDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator[Any],
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

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        if not (last_state := await self.async_get_last_state()):
            return
        self._attr_is_on = last_state.state == STATE_ON
        self.entity_description.set_fn(self.coordinator, self._attr_is_on)

    async def async_update(self) -> None:
        """Update the entity state."""


class MammotionConfigAreaSwitchEntity(MammotionBaseEntity, SwitchEntity, RestoreEntity):
    """Mammotion Config Area Switch Entity."""

    entity_description: MammotionConfigAreaSwitchEntityDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator[Any],
        entity_description: MammotionConfigAreaSwitchEntityDescription,
    ) -> None:
        """Initialize the area switch entity."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self.area = entity_description.area
        self._attr_extra_state_attributes = {"hash": self.area}
        self._attr_is_on = self.area in self.coordinator.operation_settings.areas
        # Last custom name we pushed to the device, so an unrelated registry
        # update (icon, area assignment, …) doesn't re-send set_area_name.
        self._pushed_name: str | None = None

    def update_name(self, new_name: str) -> None:
        """Update the display name when the device provides a real name for this area."""
        self.entity_description = dataclass_replace(
            self.entity_description,
            name=new_name,
            translation_key=_area_translation_key(self.coordinator.hass, new_name),
            translation_placeholders={"name": new_name},
        )
        invalidate_cached_name(self)
        # Don't overwrite _pushed_name when the user has set their own HA label —
        # resetting it to a device/auto name would cause a spurious set_area_name
        # push the next time async_registry_entry_updated fires.
        registry_entry = getattr(self, "registry_entry", None)
        if not (registry_entry and registry_entry.name):
            self._pushed_name = new_name
        if self.hass is not None:
            self.async_write_ha_state()

    def update_area(self, new_area_id: int) -> None:
        """Update the area hash when the device reports a new hash for the same named area."""
        old_area = self.area
        self.area = new_area_id
        self._attr_extra_state_attributes = {"hash": new_area_id}
        # Re-key the unique_id to the new hash, or the next restart mints a
        # duplicate entity with a "_2" suffixed entity_id.
        new_unique_id = _area_unique_id(self.coordinator, new_area_id)
        if (
            self.hass is None
            or self.registry_entry is None
            or _async_rekey_area_unique_id(
                er.async_get(self.hass), self.registry_entry.entity_id, new_unique_id
            )
        ):
            self._attr_unique_id = new_unique_id
        if old_area in self.coordinator.operation_settings.areas:
            self.coordinator.operation_settings.areas.remove(old_area)
            if new_area_id not in self.coordinator.operation_settings.areas:
                self.coordinator.operation_settings.areas.append(new_area_id)
        self._attr_is_on = new_area_id in self.coordinator.operation_settings.areas
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        self._attr_is_on = True
        self.entity_description.set_fn(self.coordinator, True, self.area)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        self._attr_is_on = False
        self.entity_description.set_fn(self.coordinator, False, self.area)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        await super().async_added_to_hass()
        # Seed with any existing name override so we only push live user edits.
        label = self.registry_entry.name if self.registry_entry else None
        self._pushed_name = self._mower_name(label) if label else None
        last_state = await self.async_get_last_state()
        if last_state and last_state.state == STATE_ON:
            await self.async_turn_on()

    @callback
    def async_registry_entry_updated(self) -> None:
        """Push a user-edited entity name to the device as this area's name."""
        super().async_registry_entry_updated()
        # Pushing area names back to the device is only supported on Luba Pro
        # (Luba 2) and newer models.
        if not DeviceType.is_luba_pro(self.coordinator.device_name):
            return
        if self.registry_entry and (label := self.registry_entry.name):
            if not (new_name := self._mower_name(label)):
                LOGGER.debug(
                    "%s: area %s label %r is only the area word; not renamed",
                    self.coordinator.device_name,
                    self.area,
                    label,
                )
                return
            if new_name == self._pushed_name:
                return
            self.hass.async_create_task(self._async_push_name(new_name))

    def _mower_name(self, label: str) -> str:
        """Return the area name for the mower: the label without HA's area word."""
        stripped = strip_prefix_word(self.hass, SWITCH_DOMAIN, "area", label)
        return label if stripped is None else stripped

    async def _async_push_name(self, new_name: str) -> None:
        """Rename the area on the mower; a failed push is retried on the next rename."""
        try:
            await self.coordinator.async_set_area_name(self.area, new_name)
        except HomeAssistantError as exc:
            LOGGER.warning(
                "%s: area %s was not renamed to %r on the mower: %s",
                self.coordinator.device_name,
                self.area,
                new_name,
                exc,
            )
            return
        self._pushed_name = new_name

    async def async_update(self) -> None:
        """Update the entity state."""
        self._attr_is_on = self.area in self.coordinator.operation_settings.areas
        area_keys: set[int] = {
            int(k)
            for k in self.coordinator.data.map.area
            if str(k).lstrip("-").isdigit()
        }
        if self.area not in area_keys:
            await self.async_remove()
            return
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return True


@callback
def async_add_area_entities(  # noqa: C901
    coordinator: MammotionReportUpdateCoordinator,
    added_areas: set[int],
    area_entities_by_name: dict[str, MammotionConfigAreaSwitchEntity],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Handle addition of mowing areas."""
    if coordinator.data is None:
        return

    switch_entities: list[MammotionConfigAreaSwitchEntity] = []
    computed = coordinator.data.map.computed_areas
    all_current_areas = {a.hash for a in computed}
    map_area_hashes: set[int] = {
        int(k) for k in coordinator.data.map.area if str(k).lstrip("-").isdigit()
    }

    # Trigger re-fetch when the device hasn't yet sent names for all areas.
    # Luba 1 / Yuka never provides area_name, so skip for it.
    if not DeviceType.is_luba1(coordinator.device_name):
        area_name_hashes: set[int] = {a.hash for a in coordinator.data.map.area_name}
        if map_area_hashes - area_name_hashes:
            coordinator.hass.async_create_task(coordinator.async_get_area_list())

    # Early exit when neither the set of area hashes nor any name has changed.
    if all_current_areas == added_areas:
        entities_by_area = {e.area: n for n, e in area_entities_by_name.items()}
        if all(entities_by_area.get(a.hash) == a.name for a in computed):
            return

    # Pre-clear auto-generated names for areas about to be removed so that
    # surviving areas can be renumbered into the freed slots without collision.
    if map_area_hashes:
        for old_hash in added_areas - all_current_areas:
            for n in [
                n
                for n, e in list(area_entities_by_name.items())
                if e.area == old_hash and _PYMAMMOTION_AUTO_NAME.match(n)
            ]:
                del area_entities_by_name[n]

    def set_area_entity(
        coord: MammotionReportUpdateCoordinator, bool_val: bool, value: int
    ) -> None:
        if bool_val:
            if value not in coord.operation_settings.areas:
                coord.operation_settings.areas.append(value)
        elif value in coord.operation_settings.areas:
            coord.operation_settings.areas.remove(value)

    entities_by_hash: dict[int, tuple[str, MammotionConfigAreaSwitchEntity]] = {
        e.area: (name, e) for name, e in area_entities_by_name.items()
    }

    registry = er.async_get(coordinator.hass)
    stale_registry_entries = _stale_area_registry_entries(
        registry, coordinator, added_areas | all_current_areas
    )

    for entry in computed:
        area_id = entry.hash
        new_name = entry.name

        if area_id in added_areas:
            # Already tracked — update name unless we'd overwrite a real device name
            # with an auto-generated one (protects user-visible names from renumbering).
            if area_id in entities_by_hash:
                current_name, entity = entities_by_hash[area_id]
                if current_name != new_name:
                    is_new_auto = bool(_PYMAMMOTION_AUTO_NAME.match(new_name))
                    is_cur_auto = bool(_PYMAMMOTION_AUTO_NAME.match(current_name))
                    if not (is_new_auto and not is_cur_auto):
                        if current_name in area_entities_by_name:
                            del area_entities_by_name[current_name]
                        entity.update_name(new_name)
                        area_entities_by_name[new_name] = entity
            continue

        # Not yet tracked — for real (non-auto) names, update the existing entity's
        # hash if the same name already exists (same logical area, device rebuilt it).
        if (
            not _PYMAMMOTION_AUTO_NAME.match(new_name)
            and new_name in area_entities_by_name
        ):
            existing = area_entities_by_name[new_name]
            added_areas.discard(existing.area)
            existing.update_area(area_id)
            added_areas.add(area_id)
            continue

        # Missing area — re-key a stale registry entry with a matching name so
        # the previous session's entity_id and customisations are reused, then
        # add a new entity with the name supplied by computed_areas.
        _async_rekey_stale_entry_for_area(
            registry, stale_registry_entries, coordinator, area_id, new_name
        )
        base_area_switch_entity = MammotionConfigAreaSwitchEntityDescription(
            key=f"{area_id}",
            translation_key=_area_translation_key(coordinator.hass, new_name),
            translation_placeholders={"name": new_name},
            area=area_id,
            name=new_name,
            set_fn=set_area_entity,
        )
        entity = MammotionConfigAreaSwitchEntity(coordinator, base_area_switch_entity)
        switch_entities.append(entity)
        area_entities_by_name[new_name] = entity
        added_areas.add(area_id)

    # Guard: only remove when map.area is non-empty — an empty map is a transient
    # refresh state and must not wipe the entity registry.
    if map_area_hashes:
        old_areas = added_areas - all_current_areas
        if old_areas:
            async_remove_stale_area_entities(coordinator, old_areas)
            for area in old_areas:
                added_areas.discard(area)
                for n in [
                    n for n, e in list(area_entities_by_name.items()) if e.area == area
                ]:
                    del area_entities_by_name[n]

    if switch_entities:
        async_add_entities(switch_entities)


def async_remove_stale_area_entities(
    coordinator: MammotionBaseUpdateCoordinator[Any],
    old_areas: set[int],
) -> None:
    """Remove area switch sensors from Home Assistant."""
    registry = er.async_get(coordinator.hass)

    for area in old_areas:
        entity_id = registry.async_get_entity_id(
            SWITCH_DOMAIN, DOMAIN, _area_unique_id(coordinator, area)
        )
        if entity_id:
            registry.async_remove(entity_id)


class MammotionSpinoSwitchEntity(MammotionBaseSpinoEntity, SwitchEntity):
    """Representation of a Mammotion Spino pool cleaner switch entity."""

    entity_description: MammotionSpinoSwitchEntityDescription

    def __init__(
        self,
        coordinator: MammotionSpinoCoordinator,
        entity_description: MammotionSpinoSwitchEntityDescription,
    ) -> None:
        """Initialize the Spino switch entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def is_on(self) -> bool:
        """Return True if the toggle is on."""
        return self.entity_description.is_on_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the toggle on.

        No explicit refresh — the device echoes the new value in a
        ``bidire_comm_cmd`` response, which the reducer applies and the
        coordinator's ``_on_state_changed`` callback pushes to this entity.
        """
        await self.entity_description.set_fn(self.coordinator, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the toggle off (state updates via the device's response event)."""
        await self.entity_description.set_fn(self.coordinator, False)
