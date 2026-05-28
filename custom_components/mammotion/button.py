"""Mammotion button sensor entities."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from functools import partial

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.hash_list import Plan
from pymammotion.data.model.pool_state import PoolPlan
from pymammotion.transport.base import TransportType
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .const import CONF_MOVEMENT_USE_WIFI, DOMAIN
from .coordinator import (
    MammotionBaseUpdateCoordinator,
    MammotionReportUpdateCoordinator,
    MammotionSpinoCoordinator,
)
from .entity import MammotionBaseEntity, MammotionBaseSpinoEntity


@dataclass(frozen=True, kw_only=True)
class MammotionButtonSensorEntityDescription(ButtonEntityDescription):
    """Describes Mammotion button sensor entity."""

    press_fn: Callable[[MammotionBaseUpdateCoordinator], Awaitable[None]]
    available_fn: Callable[[MammotionBaseUpdateCoordinator], bool] | None = None


@dataclass(frozen=True, kw_only=True)
class MammotionTaskButtonSensorEntityDescription(ButtonEntityDescription):
    """Describes Mammotion button sensor entity."""

    plan_id: str
    press_fn: Callable[[MammotionBaseUpdateCoordinator, str], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoButtonEntityDescription(ButtonEntityDescription):
    """Describes a Mammotion Spino pool cleaner button entity."""

    press_fn: Callable[[MammotionSpinoCoordinator], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoTaskButtonEntityDescription(ButtonEntityDescription):
    """Describes a dynamic per-schedule Spino task button entity.

    Mirror of :class:`MammotionTaskButtonSensorEntityDescription` for the
    Spino pool cleaner. Spino plans are keyed by a 64-bit ``jobid``; we
    stringify it for ``key`` / ``unique_id`` so it survives the HA entity
    registry's string-only constraint.
    """

    jobid: int
    press_fn: Callable[[MammotionSpinoCoordinator, int], Awaitable[None]]


SPINO_BUTTON_SENSORS: tuple[MammotionSpinoButtonEntityDescription, ...] = (
    MammotionSpinoButtonEntityDescription(
        key="spino_fetch_map",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_fetch_pool_map(),
    ),
    MammotionSpinoButtonEntityDescription(
        key="spino_fetch_line",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_fetch_pool_line(),
    ),
    MammotionSpinoButtonEntityDescription(
        key="spino_refresh_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda coordinator: coordinator.async_request_status(),
    ),
)


def _nudge_available(coordinator: MammotionBaseUpdateCoordinator) -> bool:
    """Return True when movement via BLE or Wi-Fi is possible."""
    if coordinator.config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False):
        return True
    handle = coordinator.manager.mower(coordinator.device_name)
    if handle is None:
        return False
    ble = handle.get_transport(TransportType.BLE)
    return ble is not None and ble.is_usable


BUTTON_SENSORS: tuple[MammotionButtonSensorEntityDescription, ...] = (
    MammotionButtonSensorEntityDescription(
        key="start_map_sync",
        press_fn=lambda coordinator: coordinator.async_sync_maps(),
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionButtonSensorEntityDescription(
        key="start_schedule_sync",
        press_fn=lambda coordinator: coordinator.async_sync_schedule(),
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionButtonSensorEntityDescription(
        key="resync_rtk_dock",
        press_fn=lambda coordinator: coordinator.async_rtk_dock_location(),
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionButtonSensorEntityDescription(
        key="release_from_dock",
        press_fn=lambda coordinator: coordinator.async_leave_dock(),
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_forward",
        press_fn=lambda coordinator: coordinator.async_move_forward(
            0.4,
            coordinator.config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False),
        ),
        available_fn=_nudge_available,
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_left",
        press_fn=lambda coordinator: coordinator.async_move_left(
            0.4,
            coordinator.config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False),
        ),
        available_fn=_nudge_available,
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_right",
        press_fn=lambda coordinator: coordinator.async_move_right(
            0.4,
            coordinator.config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False),
        ),
        available_fn=_nudge_available,
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_back",
        press_fn=lambda coordinator: coordinator.async_move_back(
            0.4,
            coordinator.config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False),
        ),
        available_fn=_nudge_available,
    ),
    MammotionButtonSensorEntityDescription(
        key="cancel_task",
        press_fn=lambda coordinator: coordinator.async_cancel_task(),
    ),
    MammotionButtonSensorEntityDescription(
        key="relocate_charging_station",
        press_fn=lambda coordinator: coordinator.async_relocate_charging_station(),
    ),
    # delete_charge_point
)

BUTTON_LUBA_PRO_YUKA: tuple[MammotionButtonSensorEntityDescription, ...] = (
    MammotionButtonSensorEntityDescription(
        key="restart_mower",
        press_fn=lambda coordinator: coordinator.async_restart_mower(),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion button sensor entity."""
    mammotion_devices = entry.runtime_data.mowers

    for mower in mammotion_devices:
        added_tasks: set[str] = set()
        task_entities_by_id: dict[str, MammotionTaskButtonSensorEntity] = {}

        coordinator = mower.reporting_coordinator

        update_tasks = partial(
            async_add_task_entities,
            coordinator,
            added_tasks,
            task_entities_by_id,
            async_add_entities,
        )

        update_tasks()
        coordinator.async_add_listener(update_tasks)

        async_add_entities(
            MammotionButtonSensorEntity(mower.reporting_coordinator, entity_description)
            for entity_description in BUTTON_SENSORS
        )

        if not DeviceType.is_luba1(mower.device.device_name):
            async_add_entities(
                MammotionButtonSensorEntity(
                    mower.reporting_coordinator, entity_description
                )
                for entity_description in BUTTON_LUBA_PRO_YUKA
            )

    for spino in entry.runtime_data.spino:
        async_add_entities(
            MammotionSpinoButtonEntity(spino.coordinator, entity_description)
            for entity_description in SPINO_BUTTON_SENSORS
        )

        # Dynamic per-schedule task buttons — mirrors the mower setup but
        # keyed by Spino ``jobid`` (int).  Primary purpose: provide an
        # addressable HA entity so the rename / enable / delete / copy
        # services can target a specific schedule via entity_id.
        added_spino_tasks: set[int] = set()
        spino_task_entities_by_id: dict[int, MammotionSpinoTaskButtonEntity] = {}
        update_spino_tasks = partial(
            async_add_spino_task_entities,
            spino.coordinator,
            added_spino_tasks,
            spino_task_entities_by_id,
            async_add_entities,
        )
        update_spino_tasks()
        spino.coordinator.async_add_listener(update_spino_tasks)


class MammotionButtonSensorEntity(MammotionBaseEntity, ButtonEntity):
    """Mammotion button sensor entity."""

    entity_description: MammotionButtonSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionButtonSensorEntityDescription,
    ) -> None:
        """Initialize the button sensor entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if self.entity_description.available_fn is not None:
            return super().available and self.entity_description.available_fn(
                self.coordinator
            )
        return super().available

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.entity_description.press_fn(self.coordinator)


class MammotionTaskButtonSensorEntity(MammotionBaseEntity, ButtonEntity):
    """Mammotion button sensor entity."""

    entity_description: MammotionTaskButtonSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionTaskButtonSensorEntityDescription,
    ) -> None:
        """Initialize the button task sensor entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._attr_extra_state_attributes = {"task_id": entity_description.plan_id}

    def update_name(self, new_name: str) -> None:
        """Update the display name when the plan's task_name changes."""
        self.entity_description = dataclass_replace(
            self.entity_description,
            name=new_name,
            translation_placeholders={"name": new_name},
        )
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_press(self) -> None:
        """Trigger a one-time task."""
        await self.entity_description.press_fn(
            self.coordinator, self.entity_description.plan_id
        )


def _update_task_names(
    coordinator: MammotionReportUpdateCoordinator,
    added_tasks: set[str],
    task_entities_by_id: dict[str, MammotionTaskButtonSensorEntity],
) -> None:
    """Rename task button entities whose plan task_name has changed."""
    for task_id in added_tasks:
        plan: Plan | None = coordinator.data.map.plan.get(task_id)
        if plan is None:
            continue
        entity = task_entities_by_id.get(task_id)
        if entity is None:
            continue
        if entity.entity_description.name != plan.task_name:
            entity.update_name(plan.task_name)


@callback
def async_add_task_entities(
    coordinator: MammotionReportUpdateCoordinator,
    added_tasks: set[str],
    task_entities_by_id: dict[str, MammotionTaskButtonSensorEntity],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Handle addition of mowing task buttons."""

    if coordinator.data is None:
        return

    button_entities: list[MammotionTaskButtonSensorEntity] = []
    tasks = list(map(str, coordinator.data.map.plan.keys()))
    new_tasks = set(tasks) - added_tasks

    if new_tasks:
        for task_id in new_tasks:
            existing_plan: Plan | None = next(
                (
                    plan
                    for plan in coordinator.data.map.plan.values()
                    if plan.plan_id == task_id
                ),
                None,
            )

            if existing_plan is None:
                del coordinator.data.map.plan[task_id]
                return

            base_plan_button_entity = MammotionTaskButtonSensorEntityDescription(
                key=task_id,
                translation_key="task",
                translation_placeholders={"name": existing_plan.task_name},
                plan_id=task_id,
                name=existing_plan.task_name,
                press_fn=lambda coord, value: (coord.start_task(value)),
            )
            entity = MammotionTaskButtonSensorEntity(
                coordinator, base_plan_button_entity
            )
            button_entities.append(entity)
            task_entities_by_id[task_id] = entity
            added_tasks.add(task_id)

    _update_task_names(coordinator, added_tasks, task_entities_by_id)

    old_tasks = set(tasks) - added_tasks
    if old_tasks:
        async_remove_entities(coordinator, old_tasks)
        for plan in old_tasks:
            added_tasks.remove(plan)
            task_entities_by_id.pop(plan, None)
    if button_entities:
        async_add_entities(button_entities)


def async_remove_entities(
    coordinator: MammotionBaseUpdateCoordinator,
    old_tasks: set[str],
) -> None:
    """Remove task buttons from Home Assistant."""
    registry = er.async_get(coordinator.hass)
    for task in old_tasks:
        entity_id = registry.async_get_entity_id(
            BUTTON_DOMAIN, DOMAIN, f"{coordinator.device_name}_{task}"
        )
        if entity_id:
            registry.async_remove(entity_id)


class MammotionSpinoTaskButtonEntity(MammotionBaseSpinoEntity, ButtonEntity):
    """Per-schedule Spino task button.

    Exists primarily so the schedule-modify services (rename / enable /
    disable / delete / copy / edit) can target an addressable HA entity
    via ``entity_id``.

    Spino does not expose a "start this schedule now" command in the
    proto we have today, so the press triggers a refresh of the whole
    schedule list — a useful default and the closest analogue to the
    mower's ``start_task`` press semantics.
    """

    entity_description: MammotionSpinoTaskButtonEntityDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: MammotionSpinoCoordinator,
        entity_description: MammotionSpinoTaskButtonEntityDescription,
    ) -> None:
        """Initialize the Spino task button entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = "spino_task"
        # ``task_id`` mirrors the mower entity's attribute name so the
        # service resolution helper can read it generically.  Stored as a
        # string for HA-attribute compatibility; the int form is exposed
        # via ``jobid`` for callers that prefer it.
        self._attr_extra_state_attributes = {
            "task_id": str(entity_description.jobid),
            "jobid": entity_description.jobid,
        }

    def update_name(self, new_name: str) -> None:
        """Update the display name when the plan's jobname changes."""
        self.entity_description = dataclass_replace(
            self.entity_description,
            name=new_name,
            translation_placeholders={"name": new_name},
        )
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_press(self) -> None:
        """Refresh all Spino schedules from the device.

        Spino has no per-schedule "execute now" command, so the press
        action triggers a full schedule re-sync (matching the spirit of
        the mower task button while staying within the proto we support).
        """
        await self.entity_description.press_fn(
            self.coordinator, self.entity_description.jobid
        )


def _update_spino_task_names(
    coordinator: MammotionSpinoCoordinator,
    added_tasks: set[int],
    task_entities_by_id: dict[int, MammotionSpinoTaskButtonEntity],
) -> None:
    """Rename Spino task button entities whose plan jobname has changed."""
    for jobid in added_tasks:
        plan: PoolPlan | None = coordinator.data.plans.get(jobid)
        if plan is None:
            continue
        entity = task_entities_by_id.get(jobid)
        if entity is None:
            continue
        if entity.entity_description.name != plan.jobname:
            entity.update_name(plan.jobname)


@callback
def async_add_spino_task_entities(
    coordinator: MammotionSpinoCoordinator,
    added_tasks: set[int],
    task_entities_by_id: dict[int, MammotionSpinoTaskButtonEntity],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sync the per-schedule Spino task buttons against ``coordinator.data.plans``.

    Mirror of :func:`async_add_task_entities` for the Spino path — adds a
    button when a new ``jobid`` appears, renames when ``jobname`` changes,
    and removes via the entity registry when a plan disappears.
    """
    if coordinator.data is None:
        return

    button_entities: list[MammotionSpinoTaskButtonEntity] = []
    current = set(coordinator.data.plans.keys())
    new_tasks = current - added_tasks

    for jobid in new_tasks:
        plan = coordinator.data.plans.get(jobid)
        if plan is None:
            continue
        desc = MammotionSpinoTaskButtonEntityDescription(
            key=str(jobid),
            jobid=jobid,
            name=plan.jobname,
            translation_placeholders={"name": plan.jobname},
            press_fn=lambda coord, _jobid: coord.async_refresh_spino_tasks(),
        )
        entity = MammotionSpinoTaskButtonEntity(coordinator, desc)
        button_entities.append(entity)
        task_entities_by_id[jobid] = entity
        added_tasks.add(jobid)

    _update_spino_task_names(coordinator, added_tasks, task_entities_by_id)

    old_tasks = added_tasks - current
    if old_tasks:
        registry = er.async_get(coordinator.hass)
        for jobid in old_tasks:
            entity_id = registry.async_get_entity_id(
                BUTTON_DOMAIN, DOMAIN, f"{coordinator.device_name}_{jobid}"
            )
            if entity_id:
                registry.async_remove(entity_id)
            task_entities_by_id.pop(jobid, None)
        added_tasks -= old_tasks

    if button_entities:
        async_add_entities(button_entities)


class MammotionSpinoButtonEntity(MammotionBaseSpinoEntity, ButtonEntity):
    """Mammotion Spino pool cleaner button entity."""

    entity_description: MammotionSpinoButtonEntityDescription

    def __init__(
        self,
        coordinator: MammotionSpinoCoordinator,
        entity_description: MammotionSpinoButtonEntityDescription,
    ) -> None:
        """Initialize the Spino button entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.entity_description.press_fn(self.coordinator)
