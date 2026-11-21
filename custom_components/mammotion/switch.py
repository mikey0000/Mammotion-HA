"""Support for Mammotion switches."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from functools import partial
from typing import Any, cast
from weakref import WeakKeyDictionary

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
    supports_child_safety,
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

# pymammotion's "Area N" label for an unnamed area (older versions stored "area N").
_PYMAMMOTION_AUTO_NAME = re.compile(r"^area\s+\d+$", re.IGNORECASE)

#: Registry option holding the area's stable id, so a reload can adopt a re-hashed area's row.
_AREA_ID_OPTION = "area_id"


#: Saved mowing order of the areas restored so far, per coordinator.
_RESTORED_AREA_ORDER: WeakKeyDictionary[Any, dict[int, int]] = WeakKeyDictionary()


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


def _stored_area_id(reg_entry: er.RegistryEntry) -> int | None:
    """Return the stable area id saved on a registry entry, if any."""
    options = reg_entry.options.get(DOMAIN)
    return options.get(_AREA_ID_OPTION) if options else None


def _async_rekey_stale_entry_for_area(
    registry: er.EntityRegistry,
    stale_entries: list[er.RegistryEntry],
    coordinator: MammotionReportUpdateCoordinator,
    area_hash: int,
    area_name: str,
    *,
    area_id: int | None,
) -> None:
    """Re-key the stale registry entry of this area: by stable id, else by name.

    original_name is the translated "Area {name}" (or the bare name when it
    already starts with "Area"), so match on the suffix.  An entry saved with
    another area's id is never matched by name.
    """
    by_id = [
        e
        for e in stale_entries
        if area_id is not None and _stored_area_id(e) == area_id
    ]
    by_name = [
        e
        for e in stale_entries
        if _stored_area_id(e) is None
        and (reg_name := e.original_name)
        and (reg_name == area_name or reg_name.endswith(f" {area_name}"))
    ]
    if reg_entry := next(iter(by_id or by_name), None):
        _async_rekey_area_unique_id(
            registry, reg_entry.entity_id, _area_unique_id(coordinator, area_hash)
        )
        stale_entries.remove(reg_entry)


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
    area_id: int | None = None
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
    MammotionAsyncSwitchEntityDescription(
        key="off_peak_charging",
        is_on_func=lambda coordinator: (
            settings.peak_valley_charge
            if (settings := coordinator.data.mower_state.charge_settings).reported
            else None
        ),
        set_fn=lambda coordinator, value: coordinator.async_set_off_peak_charging(
            value
        ),
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


# Gated per device on the server's function list (MowerDevice.supports_child_safety).
CHILD_SAFETY_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="child_safety",
        is_on_func=lambda coordinator: coordinator.data.mower_state.child_safety,
        set_fn=lambda coordinator, value: coordinator.async_set_child_safety(value),
        entity_category=EntityCategory.CONFIG,
    ),
)

# Gated per device on DeviceType.supports_smart_sleep (device family + firmware).
SMART_SLEEP_SWITCH_ENTITIES: tuple[MammotionAsyncSwitchEntityDescription, ...] = (
    MammotionAsyncSwitchEntityDescription(
        key="eco_sleep",
        is_on_func=lambda coordinator: coordinator.data.mower_state.eco_sleep,
        set_fn=lambda coordinator, value: coordinator.async_set_eco_sleep(value),
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionAsyncSwitchEntityDescription(
        key="live_sleep",
        is_on_func=lambda coordinator: coordinator.data.mower_state.live_sleep,
        set_fn=lambda coordinator, value: coordinator.async_set_live_sleep(value),
        entity_category=EntityCategory.CONFIG,
    ),
)

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
        area_entities: dict[int, MammotionConfigAreaSwitchEntity] = {}
        coordinator = mower.reporting_coordinator

        update_areas = partial(
            async_add_area_entities, coordinator, area_entities, async_add_entities
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
            supported=partial(DeviceType.supports_smart_sleep, device_name),
            descriptions=SMART_SLEEP_SWITCH_ENTITIES,
            build=partial(MammotionSwitchEntity, coordinator),
            async_add_entities=async_add_entities,
        )

        async_add_when_supported(
            entry,
            coordinator,
            supported=lambda coordinator=coordinator: supports_child_safety(
                coordinator.device_name, coordinator.data
            ),
            descriptions=CHILD_SAFETY_SWITCH_ENTITIES,
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
        self.area_id = entity_description.area_id
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

    @property
    def area_name(self) -> str:
        """Return the area's name as pymammotion reports it."""
        return cast(str, self.entity_description.name)

    def set_area_id(self, area_id: int) -> None:
        """Record the area's stable id, on the entity and its registry entry."""
        self.area_id = area_id
        self._async_store_area_id()

    def _async_store_area_id(self) -> None:
        if (
            self.hass is None
            or self.registry_entry is None
            or self.area_id is None
            or _stored_area_id(self.registry_entry) == self.area_id
        ):
            return
        er.async_get(self.hass).async_update_entity_options(
            self.registry_entry.entity_id, DOMAIN, {_AREA_ID_OPTION: self.area_id}
        )

    def update_area(self, new_area_id: int) -> None:
        """Move the switch to the new hash the device gave the same area."""
        old_area = self.area
        self.area = new_area_id
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
        areas = self.coordinator.operation_settings.areas
        if old_area in areas:
            areas[areas.index(old_area)] = new_area_id
        if old_area in (orders := _RESTORED_AREA_ORDER.get(self.coordinator, {})):
            orders[new_area_id] = orders.pop(old_area)
        self._attr_is_on = new_area_id in areas
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the area's hash and its place in the mowing order (1-based; None while off)."""
        areas = self.coordinator.operation_settings.areas
        order = areas.index(self.area) + 1 if self.area in areas else None
        return {"hash": self.area, "order": order}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._async_select(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._async_select(False)

    async def _async_select(self, selected: bool) -> None:
        self._attr_is_on = selected
        self.entity_description.set_fn(self.coordinator, selected, self.area)
        self.async_write_ha_state()
        # Leaving or joining the list moves every later area's place in the order.
        self.coordinator.async_update_listeners()

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        await super().async_added_to_hass()
        self._async_store_area_id()
        # Seed with any existing name override so we only push live user edits.
        label = self.registry_entry.name if self.registry_entry else None
        self._pushed_name = self._mower_name(label) if label else None
        last_state = await self.async_get_last_state()
        if last_state and last_state.state == STATE_ON:
            await self.async_turn_on()
            self._restore_order(last_state.attributes.get("order"))

    def _restore_order(self, order: object) -> None:
        """Put the area back at its saved place; entities restore in no particular order."""
        if not isinstance(order, int):
            return
        orders = _RESTORED_AREA_ORDER.setdefault(self.coordinator, {})
        orders[self.area] = order
        self.coordinator.operation_settings.areas.sort(
            key=lambda area: orders.get(area, len(orders) + 1)
        )
        self.coordinator.async_update_listeners()

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
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return True


def _set_area_selected(
    coordinator: MammotionReportUpdateCoordinator, selected: bool, area: int
) -> None:
    areas = coordinator.operation_settings.areas
    if selected and area not in areas:
        areas.append(area)
    elif not selected and area in areas:
        areas.remove(area)


def _rehashed_entity(
    gone: dict[int, MammotionConfigAreaSwitchEntity], name: str, area_id: int | None
) -> MammotionConfigAreaSwitchEntity | None:
    """Return the switch of an area that left the map, if *name*/*area_id* is that area re-hashed.

    The stable id decides; a real name is matched only against switches whose id is unknown.
    """
    by_id = [e for e in gone.values() if area_id is not None and e.area_id == area_id]
    by_name = [
        e
        for e in gone.values()
        if e.area_id is None
        and not _PYMAMMOTION_AUTO_NAME.match(name)
        and e.area_name == name
    ]
    matches = by_id or by_name
    return matches[0] if len(matches) == 1 else None


def _keeps_its_name(current: str, new: str) -> bool:
    """Whether a switch keeps *current*: same name, or a real name a fallback must not replace."""
    return current == new or (
        bool(_PYMAMMOTION_AUTO_NAME.match(new))
        and not _PYMAMMOTION_AUTO_NAME.match(current)
    )


def _new_area_entity(
    coordinator: MammotionReportUpdateCoordinator,
    area_hash: int,
    name: str,
    area_id: int | None,
) -> MammotionConfigAreaSwitchEntity:
    return MammotionConfigAreaSwitchEntity(
        coordinator,
        MammotionConfigAreaSwitchEntityDescription(
            key=f"{area_hash}",
            translation_key=_area_translation_key(coordinator.hass, name),
            translation_placeholders={"name": name},
            area=area_hash,
            area_id=area_id,
            name=name,
            set_fn=_set_area_selected,
        ),
    )


@callback
def async_add_area_entities(  # noqa: C901
    coordinator: MammotionReportUpdateCoordinator,
    area_entities: dict[int, MammotionConfigAreaSwitchEntity],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add, follow and remove the area switches, keyed by area hash in *area_entities*."""
    if coordinator.data is None:
        return

    hash_map = coordinator.data.map
    map_area_hashes: set[int] = {
        int(k) for k in hash_map.area if str(k).lstrip("-").isdigit()
    }

    # Re-fetch names while the device has not sent one for every area; a Luba 1 never does.
    if not DeviceType.is_luba1(coordinator.device_name) and map_area_hashes - {
        a.hash for a in hash_map.area_name
    }:
        coordinator.hass.async_create_task(coordinator.async_get_area_list())

    # Names left behind for deleted hashes are not areas while the map holds any.
    live = {
        a.hash: a
        for a in hash_map.computed_areas
        if not map_area_hashes or a.hash in map_area_hashes
    }
    if not live and not area_entities:
        return
    gone = {h: e for h, e in area_entities.items() if h not in live}
    registry = er.async_get(coordinator.hass)
    stale_registry_entries = _stale_area_registry_entries(
        registry, coordinator, set(area_entities) | set(live)
    )
    switch_entities: list[MammotionConfigAreaSwitchEntity] = []

    for area_hash, area in live.items():
        area_id = hash_map.area_id(area_hash)
        entity = area_entities.get(area_hash) or _rehashed_entity(
            gone, area.name, area_id
        )
        if entity is None:
            # Its old hash is still listed mid re-hash; the switch follows once it goes.
            if area_id is not None and any(
                e.area_id == area_id and e.area in live for e in area_entities.values()
            ):
                continue
            _async_rekey_stale_entry_for_area(
                registry,
                stale_registry_entries,
                coordinator,
                area_hash,
                area.name,
                area_id=area_id,
            )
            entity = _new_area_entity(coordinator, area_hash, area.name, area_id)
            switch_entities.append(entity)
            area_entities[area_hash] = entity
            continue
        if entity.area != area_hash:
            del gone[entity.area], area_entities[entity.area]
            entity.update_area(area_hash)
            area_entities[area_hash] = entity
        if area_id is not None and area_id != entity.area_id:
            entity.set_area_id(area_id)
        if not _keeps_its_name(entity.area_name, area.name):
            entity.update_name(area.name)

    # An empty map is a transient refresh state and must not wipe the registry.
    if map_area_hashes and gone:
        async_remove_stale_area_entities(coordinator, set(gone))
        for area_hash in gone:
            del area_entities[area_hash]

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
