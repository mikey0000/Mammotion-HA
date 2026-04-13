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
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .const import CONF_MOVEMENT_USE_WIFI, DOMAIN
from .coordinator import (
    MammotionBaseUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)
from .entity import MammotionBaseEntity


@dataclass(frozen=True, kw_only=True)
class MammotionButtonSensorEntityDescription(ButtonEntityDescription):
    """Describes Mammotion button sensor entity."""

    press_fn: Callable[[MammotionBaseUpdateCoordinator], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class MammotionTaskButtonSensorEntityDescription(ButtonEntityDescription):
    """Describes Mammotion button sensor entity."""

    plan_id: str
    press_fn: Callable[[MammotionBaseUpdateCoordinator, str], Awaitable[None]]


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
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_left",
        press_fn=lambda coordinator: coordinator.async_move_left(
            0.4,
            coordinator.config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False),
        ),
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_right",
        press_fn=lambda coordinator: coordinator.async_move_right(
            0.4,
            coordinator.config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False),
        ),
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_back",
        press_fn=lambda coordinator: coordinator.async_move_back(
            0.4,
            coordinator.config_entry.options.get(CONF_MOVEMENT_USE_WIFI, False),
        ),
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
    task_entities_by_id: dict[str, "MammotionTaskButtonSensorEntity"],
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
    task_entities_by_id: dict[str, "MammotionTaskButtonSensorEntity"],
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
