"""The job services still accept the retired ``rain_tactics`` field, and ignore it.

Removing it from the schemas broke every automation that still sent it, so
``start_mow`` and ``modify_running_job`` take it, drop it before it reaches the
route settings or the coordinator, and raise a repair naming the action.
"""

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.messaging.command_queue import Priority

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.lawn_mower import (
    MODIFY_RUNNING_JOB_SCHEMA,
    START_MOW_SCHEMA,
)
from tests_ha.job_service_support import make_mower_entity, start_mow


@pytest.mark.regression
async def test_start_mow_still_accepts_rain_tactics_and_ignores_it(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """Dropping the field from the schema broke every automation still sending it.

    The call must plan the job with the other fields and raise a repair naming
    the action.
    """
    entity = make_mower_entity(OperationSettings(), hass=hass)

    planned = await start_mow(entity, rain_tactics="1", speed=0.6)

    assert planned.speed == 0.6
    issue = issue_registry.async_get_issue(DOMAIN, "deprecated_rain_tactics_start_mow")
    assert issue is not None
    assert issue.translation_key == "deprecated_rain_tactics"
    assert issue.translation_placeholders == {
        "service": "mammotion.start_mow",
        "field": "rain_tactics",
    }


@pytest.mark.regression
async def test_modify_running_job_still_accepts_rain_tactics_and_ignores_it(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """The same break on the in-job editor; the coordinator gets only the live fields."""
    entity = make_mower_entity(OperationSettings(), hass=hass)

    await entity.async_modify_running_job(
        **vol.Schema(MODIFY_RUNNING_JOB_SCHEMA)({"rain_tactics": 0, "speed": 0.8})
    )

    entity.coordinator.async_modify_running_job.assert_awaited_once_with(
        priority=Priority.USER, speed=0.8
    )
    assert issue_registry.async_get_issue(
        DOMAIN, "deprecated_rain_tactics_modify_running_job"
    )


async def test_start_mow_without_rain_tactics_raises_no_repair(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """Only callers still sending the field are asked to change."""
    await start_mow(make_mower_entity(OperationSettings(), hass=hass), speed=0.6)

    assert issue_registry.issues == {}


async def test_modify_running_job_without_rain_tactics_raises_no_repair(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """Only callers still sending the field are asked to change."""
    entity = make_mower_entity(OperationSettings(), hass=hass)

    await entity.async_modify_running_job(speed=0.8)

    assert issue_registry.issues == {}


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(START_MOW_SCHEMA, id="start"),
        pytest.param(MODIFY_RUNNING_JOB_SCHEMA, id="modify"),
    ],
)
@pytest.mark.parametrize("value", [2, "x"])
def test_the_retired_field_still_only_takes_0_or_1(
    schema: dict[Any, Any], value: object
) -> None:
    """Accepting it again must not widen what the old schema allowed."""
    with pytest.raises(vol.Invalid, match="rain_tactics"):
        vol.Schema(schema)({"rain_tactics": value})
