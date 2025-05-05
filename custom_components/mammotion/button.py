"""Mammotion button sensor entities."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.hash_list import Plan

from . import MammotionConfigEntry
from .const import DOMAIN
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
        press_fn=lambda coordinator: coordinator.async_move_forward(0.4),
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_left",
        press_fn=lambda coordinator: coordinator.async_move_left(0.4),
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_right",
        press_fn=lambda coordinator: coordinator.async_move_right(0.4),
    ),
    MammotionButtonSensorEntityDescription(
        key="emergency_nudge_back",
        press_fn=lambda coordinator: coordinator.async_move_back(0.4),
    ),
    MammotionButtonSensorEntityDescription(
        key="cancel_task",
        press_fn=lambda coordinator: coordinator.async_cancel_task(),
    ),
    MammotionButtonSensorEntityDescription(
        key="clear_all_mapdata",
        press_fn=lambda coordinator: coordinator.clear_all_maps(),
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionButtonSensorEntityDescription(
        key="join_webrtc",
        press_fn=lambda coordinator: coordinator.join_webrtc_channel(),
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion button sensor entity."""
    mammotion_devices = entry.runtime_data

    for mower in mammotion_devices:
        added_tasks: set[int] = set()

        coordinator = mower.reporting_coordinator

        update_tasks = partial(
            async_add_task_entities,
            coordinator,
            added_tasks,
            async_add_entities,
        )

        update_tasks()
        coordinator.async_add_listener(update_tasks)

        async_add_entities(
            MammotionButtonSensorEntity(mower.reporting_coordinator, entity_description)
            for entity_description in BUTTON_SENSORS
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

    async def async_press(self) -> None:
        """Trigger a one-time task."""
        await self.entity_description.press_fn(
            self.coordinator, self.entity_description.plan_id
        )


@callback
def async_add_task_entities(
    coordinator: MammotionReportUpdateCoordinator,
    added_tasks: set[str],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Handle addition of mowing areas."""

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
            button_entities.append(
                MammotionTaskButtonSensorEntity(
                    coordinator,
                    base_plan_button_entity,
                )
            )
            added_tasks.add(task_id)

    old_tasks = set(tasks) - added_tasks
    if old_tasks:
        async_remove_entities(coordinator, old_tasks)
        for plan in old_tasks:
            added_tasks.remove(plan)
    if button_entities:
        async_add_entities(button_entities)


def async_remove_entities(
    coordinator: MammotionBaseUpdateCoordinator,
    old_tasks: set[str],
) -> None:
    """Remove area switch sensors from Home Assistant."""
    registry = er.async_get(coordinator.hass)
    for task in old_tasks:
        entity_id = registry.async_get_entity_id(
            BUTTON_DOMAIN, DOMAIN, f"{coordinator.device_name}_{task}"
        )
        if entity_id:
            registry.async_remove(entity_id)
