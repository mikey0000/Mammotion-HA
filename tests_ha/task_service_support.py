"""Shared scaffolding for the schedule (task) service tests: a mower holding stored plans."""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import Plan
from pymammotion.data.model.pool_state import PoolPlan
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import (
    MammotionReportUpdateCoordinator,
    MammotionSpinoCoordinator,
)
from custom_components.mammotion.services import async_setup_services

MOWER_NAME = "Luba-VS1000001"
SPINO_NAME = "Spino-S1-000001"


@dataclass
class TaskMower:
    """A wired mower: its coordinator stand-in, lawn_mower entity and one button per plan."""

    coordinator: MagicMock
    mower: str
    buttons: dict[str, str]


def setup_task_mower(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, *plans: Plan
) -> TaskMower:
    """Register the task services over a mower holding ``plans``."""
    device = MowingDevice()
    for plan in plans:
        device.map.plan[plan.plan_id] = plan
    coordinator = MagicMock(spec=MammotionReportUpdateCoordinator)
    coordinator.unique_name = MOWER_NAME
    coordinator.device_name = MOWER_NAME
    coordinator.data = device
    coordinator.running_plan = None
    coordinator.async_create_mower_task = AsyncMock()
    coordinator.async_edit_mower_task = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        mowers=[SimpleNamespace(reporting_coordinator=coordinator)], spino=[]
    )
    mower = entity_registry.async_get_or_create(
        "lawn_mower", DOMAIN, MOWER_NAME, config_entry=entry
    ).entity_id
    buttons = {
        plan.plan_id: entity_registry.async_get_or_create(
            "button", DOMAIN, f"{MOWER_NAME}_{plan.plan_id}", config_entry=entry
        ).entity_id
        for plan in plans
    }
    async_setup_services(hass)
    return TaskMower(coordinator, mower, buttons)


def setup_task_spino(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, plan: PoolPlan
) -> tuple[MagicMock, str]:
    """Register the task services over a Spino holding ``plan``; return it and the plan's button."""
    coordinator = MagicMock(spec=MammotionSpinoCoordinator)
    coordinator.unique_name = SPINO_NAME
    coordinator.data = SimpleNamespace(plans={plan.jobid: plan})
    coordinator.async_create_spino_task = AsyncMock()
    coordinator.async_edit_spino_task = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        mowers=[], spino=[SimpleNamespace(coordinator=coordinator)]
    )
    button = entity_registry.async_get_or_create(
        "button", DOMAIN, f"{SPINO_NAME}_{plan.jobid}", config_entry=entry
    ).entity_id
    async_setup_services(hass)
    return coordinator, button
