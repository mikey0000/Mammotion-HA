"""start_mow and modify_running_job carry the route fields the switch and number set.

``auto_change_direction`` and ``ride_boundary_distance`` are planning settings a
user can also pass per call.  pymammotion's route builder gates both on the
model (and firmware / border laps), so the service layer only has to deliver
them; leaving them out must keep what the switch and number entities stored.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.messaging.command_queue import Priority
from pymammotion.utility.constant.device_constant import WorkMode

from custom_components.mammotion import lawn_mower as lawn_mower_platform
from custom_components.mammotion.lawn_mower import (
    MODIFY_RUNNING_JOB_SCHEMA,
    START_MOW_SCHEMA,
    MammotionLawnMowerEntity,
)
from tests_ha.job_service_support import call_start_mow, make_mower_entity, start_mow

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_NEW_START_FIELDS = ("auto_change_direction", "ride_boundary_distance")


async def test_start_mow_plans_with_the_supplied_route_fields() -> None:
    """Both fields reach the route builder as given."""
    planned = await start_mow(
        make_mower_entity(OperationSettings()),
        auto_change_direction=1,
        ride_boundary_distance=0.3,
    )

    assert planned.auto_change_direction == 1
    assert planned.ride_boundary_distance == 0.3


async def test_start_mow_without_the_fields_keeps_the_entity_settings() -> None:
    """No schema default may overwrite what the switch and number stored."""
    stored = OperationSettings(auto_change_direction=1, ride_boundary_distance=0.5)

    planned = await start_mow(make_mower_entity(stored), speed=0.6)

    assert planned.auto_change_direction == 1
    assert planned.ride_boundary_distance == 0.5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auto_change_direction", 2),
        ("ride_boundary_distance", 1.5),
        ("ride_boundary_distance", -0.1),
    ],
)
def test_start_mow_rejects_out_of_range_values(field: str, value: float) -> None:
    """auto_change_direction is 0/1; the distance is a fraction from 0 to 1."""
    with pytest.raises(vol.Invalid):
        vol.Schema(START_MOW_SCHEMA)({field: value})


def test_start_mow_coerces_the_distance_to_float() -> None:
    """A YAML caller may send an int or a string."""
    validated = vol.Schema(START_MOW_SCHEMA)({"ride_boundary_distance": "1"})
    assert validated["ride_boundary_distance"] == 1.0
    assert isinstance(validated["ride_boundary_distance"], float)


async def test_every_registered_entity_service_exists_on_the_entity() -> None:
    """HA resolves ``func`` by name on the entity; a missing one fails every call."""
    register = MagicMock()
    entry = MagicMock()
    entry.runtime_data.mowers = []
    with patch.object(
        lawn_mower_platform.service,
        "async_register_platform_entity_service",
        register,
    ):
        await lawn_mower_platform.async_setup_entry(MagicMock(), entry, MagicMock())

    funcs = [call.kwargs["func"] for call in register.call_args_list]
    assert "async_modify_running_job" in funcs
    assert [f for f in funcs if not hasattr(MammotionLawnMowerEntity, f)] == []


async def test_modify_running_job_forwards_the_fields_to_the_coordinator() -> None:
    """Every field the schema accepts, auto_change_direction included, is handed on."""
    entity = make_mower_entity(OperationSettings())
    data = vol.Schema(MODIFY_RUNNING_JOB_SCHEMA)(
        {"auto_change_direction": "1", "speed": 0.8}
    )

    await entity.async_modify_running_job(**data)

    entity.coordinator.async_modify_running_job.assert_awaited_once_with(
        priority=Priority.USER, auto_change_direction=1, speed=0.8
    )


def test_services_yaml_declares_the_start_mow_fields() -> None:
    """Optional, with no default, and bounded like the modify and task fields."""
    fields = yaml.safe_load((_ROOT / "services.yaml").read_text())["start_mow"][
        "fields"
    ]
    for name in _NEW_START_FIELDS:
        assert fields[name]["required"] is False, name
        assert "default" not in fields[name], name
    assert fields["auto_change_direction"]["selector"]["number"] == {
        "min": 0,
        "max": 1,
        "step": 1,
    }
    distance = fields["ride_boundary_distance"]["selector"]["number"]
    assert (distance["min"], distance["max"], distance["step"]) == (0, 1, 0.1)


def test_the_start_mow_fields_are_translated_in_every_locale() -> None:
    """strings.json plus every locale, in each locale's own language."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 2
    for path in files:
        fields = json.loads(path.read_text(encoding="utf-8"))["services"]["start_mow"][
            "fields"
        ]
        for name in _NEW_START_FIELDS:
            field = fields[name]
            assert field["name"] and field["description"], (path.name, name)
            if path.stem not in ("en", "strings"):
                en_field = english["services"]["start_mow"]["fields"][name]
                assert field["name"] != en_field["name"], (path.name, name)
                assert field["description"] != en_field["description"], (
                    path.name,
                    name,
                )


_STORED = OperationSettings(
    is_dump=False,
    collect_grass_frequency=20,
    border_mode=0,
    speed=0.2,
    ultra_wave=10,
    channel_mode=2,
    channel_width=20,
    blade_height=50,
    toward=45,
    toward_included_angle=30,
    toward_mode=1,
    mowing_laps=3,
    obstacle_laps=3,
    start_progress=40,
    areas=[333],
)


def test_start_mow_fills_in_no_route_field_the_caller_left_out() -> None:
    """HA applies schema defaults before the entity sees the call (#913)."""
    validated = vol.Schema(START_MOW_SCHEMA)({})

    assert set(validated) <= {"modify", "plan_only"}


async def test_start_mow_keeps_the_entity_settings_it_was_not_given() -> None:
    """The reporter's automation: areas plus two fields, and Working speed must stand.

    The schema used to default speed to 0.3, ultra_wave to 2 (not even an option on
    a Yuka Mini), border_mode to 1 and the rest, overwriting the config entities.
    """
    planned = await start_mow(
        make_mower_entity(dataclasses.replace(_STORED)),
        areas=["switch.area_front"],
        channel_width=8,
        mowing_laps=2,
    )

    assert planned == dataclasses.replace(
        _STORED, areas=[111], channel_width=8, mowing_laps=2
    )


async def test_start_mow_without_areas_keeps_the_switch_selection() -> None:
    """Omitting areas is like omitting any other field: the area switches stand."""
    planned = await start_mow(
        make_mower_entity(dataclasses.replace(_STORED)), speed=0.6
    )

    assert planned.areas == [333]
    assert planned.speed == 0.6


def test_services_yaml_shows_no_default_for_the_route_fields() -> None:
    """A UI default the call no longer applies would misstate what gets sent."""
    fields = yaml.safe_load((_ROOT / "services.yaml").read_text())["start_mow"][
        "fields"
    ]
    assert [
        name
        for name, field in fields.items()
        if "default" in field and name not in ("modify", "plan_only")
    ] == []


async def test_a_new_job_on_a_paused_mower_ends_it_then_plans_and_starts() -> None:
    """End the paused job, wait for READY, then plan and start the one asked for (#918).

    The mode was read once before cancelling, so the stale PAUSE sent
    resume_execute_task for the job just ended and never reached start_job.
    """
    entity = make_mower_entity(
        OperationSettings(),
        mode=WorkMode.MODE_PAUSE,
        bp_info=1,
        responses={"cancel_job": WorkMode.MODE_READY},
    )

    await call_start_mow(entity, areas=["switch.area_back"], speed=0.5)

    assert entity.coordinator.sent == [
        "cancel_job",
        "plan_route in MODE_READY",
        "start_job",
    ]


async def test_a_new_job_on_a_working_mower_pauses_ends_then_starts() -> None:
    """WORKING has to reach PAUSE before cancel_job, and READY before planning."""
    entity = make_mower_entity(
        OperationSettings(),
        mode=WorkMode.MODE_WORKING,
        bp_info=1,
        responses={
            "pause_execute_task": WorkMode.MODE_PAUSE,
            "cancel_job": WorkMode.MODE_READY,
        },
    )

    await call_start_mow(entity, areas=["switch.area_back"])

    assert entity.coordinator.sent == [
        "pause_execute_task",
        "cancel_job",
        "plan_route in MODE_READY",
        "start_job",
    ]


async def test_cancel_job_on_a_working_mower_ends_the_job() -> None:
    """cancel_job used to re-read a stale WORKING after pausing and never send it."""
    entity = make_mower_entity(
        OperationSettings(),
        mode=WorkMode.MODE_WORKING,
        responses={"pause_execute_task": WorkMode.MODE_PAUSE},
    )

    await entity.async_stop()

    assert entity.coordinator.sent == ["pause_execute_task", "cancel_job"]


@pytest.mark.parametrize(
    "data",
    [None, {}],
    ids=["lawn_mower.start_mowing", "mammotion.start_mow without fields"],
)
async def test_start_without_route_fields_resumes_a_paused_job(
    data: dict[str, Any] | None,
) -> None:
    """Nothing new was asked for, so the paused job is resumed, never ended (#485)."""
    entity = make_mower_entity(OperationSettings(), mode=WorkMode.MODE_PAUSE, bp_info=1)

    if data is None:
        await entity.async_start_mowing()
    else:
        await entity.async_start_mowing(**vol.Schema(START_MOW_SCHEMA)(data))

    assert entity.coordinator.sent == [
        "resume_execute_task",
        "query_generate_route_information",
    ]


async def test_a_new_job_fails_if_the_mower_never_reports_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning onto a mower still holding the old job would silently start nothing."""
    monkeypatch.setattr(lawn_mower_platform, "_MODE_WAIT_TIMEOUT", 0)
    entity = make_mower_entity(OperationSettings(), mode=WorkMode.MODE_PAUSE, bp_info=1)

    with pytest.raises(HomeAssistantError) as raised:
        await call_start_mow(entity, areas=["switch.area_back"])

    assert raised.value.translation_key == "start_failed"
    assert entity.coordinator.sent == ["cancel_job"]


def _docks_on_fresh_report(entity: MammotionLawnMowerEntity) -> None:
    """Make the fresh-state read deliver a report: the stale WORKING was a docked mower."""

    async def fresh(*, wait: bool = False) -> None:
        assert wait, "a user action must wait for the report"
        entity.coordinator.data.report_data.dev.sys_status = WorkMode.MODE_READY
        entity.coordinator.data.report_data.work.bp_info = 0

    entity.coordinator.async_ensure_fresh_state.side_effect = fresh


async def test_start_mow_acts_on_the_fresh_report_not_the_stale_mode() -> None:
    """The stale WORKING would pause and cancel a job the mower no longer runs."""
    entity = make_mower_entity(
        OperationSettings(), mode=WorkMode.MODE_WORKING, bp_info=1
    )
    _docks_on_fresh_report(entity)

    await call_start_mow(entity, areas=["switch.area_back"])

    assert entity.coordinator.sent == ["plan_route in MODE_READY", "start_job"]


async def test_cancel_acts_on_the_fresh_report_not_the_stale_mode() -> None:
    """A mower already docked has no job to end, whatever the old report said."""
    entity = make_mower_entity(OperationSettings(), mode=WorkMode.MODE_WORKING)
    _docks_on_fresh_report(entity)

    await entity.async_stop()

    assert entity.coordinator.sent == []


async def test_modify_on_a_paused_mower_modifies_without_ending_the_job() -> None:
    """modify: true re-plans the job in place; it must not cancel or start one."""
    entity = make_mower_entity(OperationSettings(), mode=WorkMode.MODE_PAUSE, bp_info=1)

    await entity.async_start_mowing(
        **vol.Schema(START_MOW_SCHEMA)({"modify": True, "speed": 0.5})
    )

    entity.coordinator.async_modify_plan_route.assert_awaited_once()
    assert entity.coordinator.async_modify_plan_route.await_args.args[0].speed == 0.5
    assert entity.coordinator.sent == []


async def test_a_new_job_on_a_docked_mower_holding_a_breakpoint_plans_it() -> None:
    """READY with a suspended job: end it, then plan and start the job asked for.

    This used to resume the suspended route and drop the requested areas. The app
    refuses to plan while a breakpoint stands, so cancel_job goes first.
    """
    entity = make_mower_entity(
        OperationSettings(),
        bp_info=1,
        responses={"cancel_job": WorkMode.MODE_READY},
    )

    await call_start_mow(entity, areas=["switch.area_back"])

    assert entity.coordinator.sent == [
        "cancel_job",
        "plan_route in MODE_READY",
        "start_job",
    ]
    assert entity.coordinator.async_plan_route.await_args.args[0].areas == [222]


async def test_start_without_route_fields_resumes_a_docked_breakpoint() -> None:
    """Nothing new asked for on a docked mower holding a breakpoint: resume it."""
    entity = make_mower_entity(OperationSettings(), bp_info=1)

    await call_start_mow(entity)

    assert entity.coordinator.sent == [
        "query_generate_route_information",
        "start_job",
    ]


async def test_an_unconfirmed_route_plan_still_starts_the_job() -> None:
    """The plan's reply often goes unmatched (#848); the mower still accepts start_job."""
    entity = make_mower_entity(
        OperationSettings(), failures={"plan_route": "command_unconfirmed"}
    )

    await call_start_mow(entity, areas=["switch.area_back"])

    assert entity.coordinator.sent == ["plan_route in MODE_READY", "start_job"]


async def test_an_unconfirmed_route_query_still_resumes_the_breakpoint() -> None:
    """Same for the route query before resuming a docked breakpoint."""
    entity = make_mower_entity(
        OperationSettings(),
        bp_info=1,
        failures={"query_generate_route_information": "command_unconfirmed"},
    )

    await call_start_mow(entity)

    assert entity.coordinator.sent == [
        "query_generate_route_information",
        "start_job",
    ]


@pytest.mark.parametrize(
    ("command", "key"),
    [("plan_route", "command_failed"), ("start_job", "command_unconfirmed")],
    ids=["planning fails outright", "start_job unconfirmed"],
)
async def test_start_mow_still_fails_on_any_other_error(command: str, key: str) -> None:
    """Only an unconfirmed planning reply is stepped over."""
    entity = make_mower_entity(OperationSettings(), failures={command: key})

    with pytest.raises(HomeAssistantError) as raised:
        await call_start_mow(entity, areas=["switch.area_back"])

    assert raised.value.translation_key == key
    assert entity.coordinator.sent[-1] == (
        "start_job" if command == "start_job" else "plan_route in MODE_READY"
    )
