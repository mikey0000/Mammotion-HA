"""The schedule settings create_task and edit_task take reach the plan unchanged.

``get_task`` returns edit_task's field names so a row can be fed back into it;
that round trip is the contract every schedule field has to keep, whichever
device kind defines it.
"""

import dataclasses

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.hash_list import Plan
from pymammotion.data.model.pool_state import PoolPlan
from task_service_support import TaskMower, setup_task_mower, setup_task_spino

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.services import (
    _SPINO_ONLY_FIELDS,
    EDIT_TASK_SCHEMA,
    SERVICE_CREATE_TASK,
    SERVICE_EDIT_TASK,
    SERVICE_GET_TASK,
)

_PLAN_ID = "202405201230451234567"
_ZONE_HASH = 10123456789012345
#: get_task names a few settings differently from the Plan attribute they hold.
_PLAN_ATTRIBUTE = {"name": "task_name", "enabled": "reserved"}


def _every_setting_changed() -> Plan:
    """Return a stored plan whose every editable setting differs from ``Plan()``.

    ``reserved`` is the device echo (+10 per byte) of an enabled plan.
    """
    return Plan(
        plan_id=_PLAN_ID,
        task_name="Front lawn",
        reserved="\x0b\x0c\x0a\x0a\x0a\x12\x14\x0a",
        weeks=[1, 3, 5],
        start_time="08:30",
        end_time="11:00",
        start_date="2026-06-01",
        end_date="2026-08-31",
        trigger_type=2,
        day=3,
        knife_height=45,
        speed=0.2,
        edge_mode=4,
        route_model=3,
        ultrasonic_barrier=11,
        ride_boundary_distance=0.5,
        auto_change_direction=False,
        route_angle=90,
        toward_mode=2,
        toward_included_angle=-45,
        route_spacing=25,
        zone_hashs=[_ZONE_HASH],
    )


def _mower_edit_fields() -> set[str]:
    """Return the edit_task fields a mower schedule takes."""
    spino_only = {str(key) for key in _SPINO_ONLY_FIELDS}
    return {str(key) for key in EDIT_TASK_SCHEMA.schema} - spino_only - {"entity_id"}


def _sent_plan(task_mower: TaskMower, method: str) -> Plan:
    """Return the plan the service handed the coordinator."""
    mock = getattr(task_mower.coordinator, method)
    mock.assert_awaited_once()
    return mock.await_args.args[0]


async def test_a_get_task_row_fed_back_to_edit_task_changes_nothing(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Every field the schema takes must be read back by get_task and written unaltered.

    A field the schema coerces to another type, that get_task leaves out or that
    the plan builder skips arrives changed here (#947: speed 0.2 sent as 0).
    """
    plan = _every_setting_changed()
    task_mower = setup_task_mower(hass, entity_registry, plan)
    untouched = sorted(
        field
        for field in _mower_edit_fields()
        if getattr(plan, _PLAN_ATTRIBUTE.get(field, field))
        == getattr(Plan(), _PLAN_ATTRIBUTE.get(field, field))
    )
    assert not untouched, f"give these a non-default value in the fixture: {untouched}"

    row = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASK,
        {"entity_id": task_mower.mower, "task_id": _PLAN_ID},
        blocking=True,
        return_response=True,
    )
    assert row.keys() >= _mower_edit_fields()
    await hass.services.async_call(DOMAIN, SERVICE_EDIT_TASK, row, blocking=True)

    sent = _sent_plan(task_mower, "async_edit_mower_task")
    assert sent.reserved_for_send() == plan.reserved_for_send()
    assert dataclasses.replace(sent, reserved="") == dataclasses.replace(
        plan, reserved=""
    )


@pytest.mark.regression
@pytest.mark.parametrize(
    ("service", "method"),
    [
        pytest.param(SERVICE_EDIT_TASK, "async_edit_mower_task", id="edit"),
        pytest.param(SERVICE_CREATE_TASK, "async_create_mower_task", id="create"),
    ],
)
async def test_a_mower_speed_below_one_metre_per_second_is_kept(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, service: str, method: str
) -> None:
    """The Spino's int speed validator replaced the mower's float one (#947).

    Both field sets defined ``speed`` and were merged with ``{**a, **b}``, so
    the later ``Coerce(int)`` won: 0.2 m/s was sent to the mower as 0.
    """
    plan = Plan(plan_id=_PLAN_ID, speed=0.4)
    task_mower = setup_task_mower(hass, entity_registry, plan)
    target = {
        SERVICE_EDIT_TASK: task_mower.buttons[_PLAN_ID],
        SERVICE_CREATE_TASK: task_mower.mower,
    }[service]

    await hass.services.async_call(
        DOMAIN,
        service,
        {"entity_id": target, "name": "Front lawn", "speed": 0.2},
        blocking=True,
    )

    assert _sent_plan(task_mower, method).speed == 0.2


@pytest.mark.regression
async def test_edit_task_accepts_the_four_perimeter_laps_the_app_sets(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """``edge_mode`` was capped at 2 by hand while BorderPatrolMode goes to 4 (#947).

    A plan set to four laps in the app read back as ``edge_mode: 4``, which
    edit_task then rejected.
    """
    plan = Plan(plan_id=_PLAN_ID, edge_mode=4)
    task_mower = setup_task_mower(hass, entity_registry, plan)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_EDIT_TASK,
        {"entity_id": task_mower.buttons[_PLAN_ID], "edge_mode": 3},
        blocking=True,
    )

    assert _sent_plan(task_mower, "async_edit_mower_task").edge_mode == 3


@pytest.mark.regression
@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("route_model", 3, id="path-pattern"),
        pytest.param("ultrasonic_barrier", 10, id="obstacle-detection"),
    ],
)
async def test_create_task_delivers_the_path_pattern_settings(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, field: str, value: int
) -> None:
    """The schema did not take these, so a new plan kept 0 whatever was asked (#947).

    The app sets both per schedule; a select in the UI sends them as text.
    """
    task_mower = setup_task_mower(hass, entity_registry)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CREATE_TASK,
        {"entity_id": task_mower.mower, "name": "Back lawn", field: str(value)},
        blocking=True,
    )

    assert getattr(_sent_plan(task_mower, "async_create_mower_task"), field) == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("edge_mode", 5, id="five-perimeter-laps"),
        pytest.param("route_model", 4, id="unknown-path-pattern"),
        pytest.param("ultrasonic_barrier", 3, id="unknown-detection"),
        pytest.param("toward_mode", 3, id="unknown-angle-mode"),
    ],
)
async def test_edit_task_rejects_a_value_the_library_enum_does_not_define(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, field: str, value: int
) -> None:
    """Nothing outside the pymammotion enum reaches the mower."""
    task_mower = setup_task_mower(hass, entity_registry, Plan(plan_id=_PLAN_ID))

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EDIT_TASK,
            {"entity_id": task_mower.buttons[_PLAN_ID], field: value},
            blocking=True,
        )

    task_mower.coordinator.async_edit_mower_task.assert_not_awaited()


async def test_edit_task_sends_a_spino_a_whole_number_speed(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The shared speed field is a float; a whole number reaches the Spino's fixed32 intact."""
    coordinator, button = setup_task_spino(
        hass, entity_registry, PoolPlan(jobid=7, speed=1)
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_EDIT_TASK, {"entity_id": button, "speed": 2.0}, blocking=True
    )

    (plan,) = coordinator.async_edit_spino_task.await_args.args
    assert type(plan.speed) is int
    assert plan.speed == 2


@pytest.mark.parametrize(
    ("service", "method"),
    [
        pytest.param(SERVICE_EDIT_TASK, "async_edit_spino_task", id="edit"),
        pytest.param(SERVICE_CREATE_TASK, "async_create_spino_task", id="create"),
    ],
)
async def test_a_fractional_spino_speed_is_refused(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, service: str, method: str
) -> None:
    """Truncating 2.7 to 2 would store a speed nobody asked for."""
    coordinator, button = setup_task_spino(
        hass, entity_registry, PoolPlan(jobid=7, speed=1)
    )

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            service,
            {"entity_id": button, "name": "Pool", "speed": 2.7},
            blocking=True,
        )

    assert err.value.translation_key == "spino_speed_not_whole"
    getattr(coordinator, method).assert_not_awaited()


async def test_create_task_refuses_a_detection_mode_the_mower_does_not_offer(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """A Luba 2 on current firmware offers 1, 10 and 11; the schema alone takes every enum value."""
    task_mower = setup_task_mower(hass, entity_registry)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_TASK,
            {
                "entity_id": task_mower.mower,
                "name": "Back lawn",
                "ultrasonic_barrier": 2,
            },
            blocking=True,
        )

    assert err.value.translation_key == "detection_strategy_unsupported"
    assert err.value.translation_placeholders["offered"] == "1, 10, 11"
    task_mower.coordinator.async_create_mower_task.assert_not_awaited()


async def test_edit_task_lets_a_schedule_keep_the_detection_mode_it_holds(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """A stored 0 is outside the offered list but the firmware accepted it; re-sending it is no change."""
    task_mower = setup_task_mower(
        hass, entity_registry, Plan(plan_id=_PLAN_ID, ultrasonic_barrier=0)
    )

    await hass.services.async_call(
        DOMAIN,
        SERVICE_EDIT_TASK,
        {"entity_id": task_mower.buttons[_PLAN_ID], "ultrasonic_barrier": 0},
        blocking=True,
    )

    assert _sent_plan(task_mower, "async_edit_mower_task").ultrasonic_barrier == 0


async def test_edit_task_refuses_changing_to_a_detection_mode_the_mower_does_not_offer(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Only the value the schedule already holds is exempt from the model check."""
    task_mower = setup_task_mower(
        hass, entity_registry, Plan(plan_id=_PLAN_ID, ultrasonic_barrier=10)
    )

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EDIT_TASK,
            {"entity_id": task_mower.buttons[_PLAN_ID], "ultrasonic_barrier": 2},
            blocking=True,
        )

    assert err.value.translation_key == "detection_strategy_unsupported"
    task_mower.coordinator.async_edit_mower_task.assert_not_awaited()
