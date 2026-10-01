"""edit_task leaves a schedule's enabled flag alone unless the call sets it.

``enabled`` once carried ``default=True`` in the field set create_task and
edit_task share, so every edit arrived with ``enabled`` present and a disabled
schedule was switched back on by an unrelated change (#913 family).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import Plan
from pymammotion.data.model.pool_state import PoolPlan
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.services import (
    CREATE_TASK_SCHEMA,
    EDIT_TASK_SCHEMA,
    async_setup_services,
)

_MOWER = "Luba-VS1000001"
_SPINO = "Spino-S1000001"
_PLAN_ID = "202405201230451234567"
_JOB_ID = 42


def _entry(hass: HomeAssistant, *, mowers: list, spino: list) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(mowers=mowers, spino=spino)
    return entry


def _mower_button(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, plan: Plan
) -> tuple[MagicMock, str]:
    device = MowingDevice()
    device.map.plan[plan.plan_id] = plan
    coordinator = MagicMock(unique_name=_MOWER, data=device, running_plan=None)
    coordinator.async_edit_mower_task = AsyncMock()
    entry = _entry(
        hass, mowers=[MagicMock(reporting_coordinator=coordinator)], spino=[]
    )
    button = entity_registry.async_get_or_create(
        "button", DOMAIN, f"{_MOWER}_{plan.plan_id}", config_entry=entry
    ).entity_id
    async_setup_services(hass)
    return coordinator, button


def _spino_button(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, plan: PoolPlan
) -> tuple[MagicMock, str]:
    coordinator = MagicMock(unique_name=_SPINO)
    coordinator.data.plans = {plan.jobid: plan}
    coordinator.async_edit_spino_task = AsyncMock()
    entry = _entry(hass, mowers=[], spino=[MagicMock(coordinator=coordinator)])
    button = entity_registry.async_get_or_create(
        "button", DOMAIN, f"{_SPINO}_{plan.jobid}", config_entry=entry
    ).entity_id
    async_setup_services(hass)
    return coordinator, button


@pytest.mark.regression
def test_edit_schema_adds_no_enabled_flag() -> None:
    """A validated edit carries only what the caller sent."""
    assert "enabled" not in EDIT_TASK_SCHEMA({"entity_id": "button.x", "day": 3})


def test_create_schema_still_enables_a_new_schedule() -> None:
    """A new schedule is on unless asked otherwise."""
    assert CREATE_TASK_SCHEMA({"entity_id": "lawn_mower.x", "name": "Front"})["enabled"]


@pytest.mark.regression
async def test_editing_a_disabled_mower_schedule_keeps_it_disabled(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Changing the blade height must not switch the schedule back on."""
    stored = Plan(plan_id=_PLAN_ID, task_name="Front").with_enabled(False)
    coordinator, button = _mower_button(hass, entity_registry, stored)

    await hass.services.async_call(
        DOMAIN, "edit_task", {"entity_id": button, "knife_height": 50}, blocking=True
    )

    (plan,) = coordinator.async_edit_mower_task.await_args.args
    assert plan.knife_height == 50
    assert not plan.is_enabled()


async def test_edit_task_still_enables_when_asked(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """An explicit enabled reaches the plan."""
    stored = Plan(plan_id=_PLAN_ID, task_name="Front").with_enabled(False)
    coordinator, button = _mower_button(hass, entity_registry, stored)

    await hass.services.async_call(
        DOMAIN, "edit_task", {"entity_id": button, "enabled": True}, blocking=True
    )

    (plan,) = coordinator.async_edit_mower_task.await_args.args
    assert plan.is_enabled()


@pytest.mark.regression
async def test_editing_a_disabled_spino_schedule_keeps_it_disabled(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The Spino path shares the schema, so it had the same fault."""
    stored = PoolPlan(jobid=_JOB_ID, jobname="Pool", enabled=False)
    coordinator, button = _spino_button(hass, entity_registry, stored)

    await hass.services.async_call(
        DOMAIN, "edit_task", {"entity_id": button, "speed": 2}, blocking=True
    )

    (plan,) = coordinator.async_edit_spino_task.await_args.args
    assert plan.speed == 2
    assert plan.enabled is False
