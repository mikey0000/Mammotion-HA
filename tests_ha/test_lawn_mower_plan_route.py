"""``mammotion.plan_route``: plan a job without starting it and hand back the route.

A card asks for this to learn the direction the mower itself picks.  The route
is fetched only after the plan is made, and a mower on a job is left alone.
"""

import asyncio
from typing import Any

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError
from job_service_support import make_mower_entity
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.utility.constant import MOWING_ACTIVE_MODES
from pymammotion.utility.constant.device_constant import WorkMode

from custom_components.mammotion.lawn_mower import (
    PLAN_ROUTE_SCHEMA,
    START_MOW_SCHEMA,
    MammotionLawnMowerEntity,
)

_TIMEOUT = 5


async def _plan(entity: MammotionLawnMowerEntity, **data: Any) -> Any:
    """Run plan_route through its own schema, as HA's service layer does."""
    return await asyncio.wait_for(
        entity.async_plan_route(**vol.Schema(PLAN_ROUTE_SCHEMA)(data)), _TIMEOUT
    )


async def test_an_idle_mower_plans_without_starting_then_returns_the_route() -> None:
    """Fetched before the plan, the route would be an earlier job's."""
    entity = make_mower_entity(OperationSettings())
    sent_before_the_fetch: list[str] = []

    async def _planned_route() -> dict[str, Any]:
        sent_before_the_fetch.extend(entity.coordinator.sent)
        return {"plan": {"toward": 30}}

    entity.coordinator.async_planned_route.side_effect = _planned_route

    response = await _plan(entity, areas=["switch.area_front"], channel_mode=0)

    assert response == {"plan": {"toward": 30}}
    assert sent_before_the_fetch == ["plan_route in MODE_READY"]
    assert entity.coordinator.sent == ["plan_route in MODE_READY"]
    planned = entity.coordinator.async_plan_route.await_args.args[0]
    assert (planned.areas, planned.channel_mode) == ([111], 0)


@pytest.mark.parametrize("mode", sorted(MOWING_ACTIVE_MODES))
async def test_a_mower_on_a_job_is_refused_and_left_alone(mode: int) -> None:
    """Planning ends the job in hand, which nobody asking for a preview wants."""
    entity = make_mower_entity(OperationSettings(), WorkMode(mode))

    with pytest.raises(ServiceValidationError) as raised:
        await _plan(entity, areas=["switch.area_front"])

    assert raised.value.translation_key == "plan_needs_idle_mower"
    assert entity.coordinator.sent == []
    entity.coordinator.async_planned_route.assert_not_awaited()


def test_it_takes_no_field_that_would_modify_a_job() -> None:
    """A plan describes a new job; it has no running one to change."""
    with pytest.raises(vol.Invalid):
        vol.Schema(PLAN_ROUTE_SCHEMA)({"modify": True})


def test_starting_a_job_no_longer_takes_a_plan_only_switch() -> None:
    """Planning without starting is this service; a second way to ask would drift from it."""
    with pytest.raises(vol.Invalid):
        vol.Schema(START_MOW_SCHEMA)({"plan_only": True})
