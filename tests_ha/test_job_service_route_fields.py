"""start_mow and modify_running_job carry the route fields the switch and number set.

``auto_change_direction`` and ``ride_boundary_distance`` are planning settings a
user can also pass per call.  pymammotion's route builder gates both on the
model (and firmware / border laps), so the service layer only has to deliver
them; leaving them out must keep what the switch and number entities stored.
"""

import asyncio
import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.utility.constant.device_constant import WorkMode

from custom_components.mammotion import lawn_mower as lawn_mower_platform
from custom_components.mammotion.lawn_mower import (
    MODIFY_RUNNING_JOB_SCHEMA,
    START_MOW_SCHEMA,
    MammotionLawnMowerEntity,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_NEW_START_FIELDS = ("auto_change_direction", "ride_boundary_distance")


def _entity(
    settings: OperationSettings,
    mode: WorkMode = WorkMode.MODE_READY,
    bp_info: int = 0,
    responses: dict[str, WorkMode] | None = None,
) -> MammotionLawnMowerEntity:
    """Build the real lawn mower entity over a mower in *mode*.

    A command named in *responses* moves the mower to that mode one loop turn
    after it is sent, reaching the entity through its coordinator listeners the
    way a pushed report does; that report clears the breakpoint after cancel_job.
    Everything sent lands in ``coordinator.sent``.
    """
    device = MowingDevice()
    device.report_data.dev.sys_status = mode
    device.report_data.dev.charge_state = 1
    device.report_data.work.bp_info = bp_info
    listeners: list[Callable[[], None]] = []
    sent: list[str] = []
    coordinator = MagicMock()
    coordinator.unique_name = "Luba-VA123456"
    coordinator.device_name = "Luba-VA123456"
    coordinator.data = device
    coordinator.operation_settings = settings
    coordinator.sent = sent

    def push(new_mode: WorkMode, job_ended: bool) -> None:
        device.report_data.dev.sys_status = new_mode
        if job_ended:
            device.report_data.work.bp_info = 0
        for listener in listeners:
            listener()

    def add_listener(listener: Callable[[], None], *_: Any) -> Callable[[], None]:
        listeners.append(listener)
        return lambda: listeners.remove(listener)

    async def send(command: str, *_: Any, **__: Any) -> None:
        sent.append(command)
        if (new_mode := (responses or {}).get(command)) is not None:
            asyncio.get_running_loop().call_soon(
                push, new_mode, command == "cancel_job"
            )

    async def plan_route(*_: Any, **__: Any) -> bool:
        sent.append(f"plan_route in {device.report_data.dev.sys_status.name}")
        return True

    coordinator.async_add_listener = add_listener
    coordinator.async_ensure_fresh_state = AsyncMock()
    coordinator.async_request_report_snapshot = AsyncMock()
    coordinator.async_start_report_stream = AsyncMock()
    coordinator.async_plan_route = AsyncMock(side_effect=plan_route)
    coordinator.async_modify_plan_route = AsyncMock(return_value=True)
    coordinator.async_send_command = AsyncMock(side_effect=send)
    coordinator.async_send_and_wait = AsyncMock(side_effect=send)
    coordinator.async_modify_running_job = AsyncMock(return_value=True)
    entity = MammotionLawnMowerEntity(coordinator)
    entity.hass = MagicMock()
    entity.hass.states.get = {
        "switch.area_front": State("switch.area_front", "on", {"hash": "111"}),
        "switch.area_back": State("switch.area_back", "on", {"hash": "222"}),
    }.get
    return entity


async def _start_mow(
    entity: MammotionLawnMowerEntity, **data: Any
) -> OperationSettings:
    """Run start_mow through its own schema and return the settings it planned with."""
    await _call_start_mow(entity, **data)
    return entity.coordinator.async_plan_route.await_args.args[0]


async def _call_start_mow(entity: MammotionLawnMowerEntity, **data: Any) -> None:
    """Run start_mow through its own schema, as HA's service layer does."""
    await entity.async_start_mowing(**vol.Schema(START_MOW_SCHEMA)(data))


async def test_start_mow_plans_with_the_supplied_route_fields() -> None:
    """Both fields reach the route builder as given."""
    planned = await _start_mow(
        _entity(OperationSettings()),
        auto_change_direction=1,
        ride_boundary_distance=0.3,
    )

    assert planned.auto_change_direction == 1
    assert planned.ride_boundary_distance == 0.3


async def test_start_mow_without_the_fields_keeps_the_entity_settings() -> None:
    """No schema default may overwrite what the switch and number stored."""
    stored = OperationSettings(auto_change_direction=1, ride_boundary_distance=0.5)

    planned = await _start_mow(_entity(stored), speed=0.6)

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
    entity = _entity(OperationSettings())
    data = vol.Schema(MODIFY_RUNNING_JOB_SCHEMA)(
        {"auto_change_direction": "1", "speed": 0.8}
    )

    await entity.async_modify_running_job(**data)

    entity.coordinator.async_modify_running_job.assert_awaited_once_with(
        auto_change_direction=1, speed=0.8
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
    planned = await _start_mow(
        _entity(dataclasses.replace(_STORED)),
        areas=["switch.area_front"],
        channel_width=8,
        mowing_laps=2,
    )

    assert planned == dataclasses.replace(
        _STORED, areas=[111], channel_width=8, mowing_laps=2
    )


async def test_start_mow_without_areas_keeps_the_switch_selection() -> None:
    """Omitting areas is like omitting any other field: the area switches stand."""
    planned = await _start_mow(_entity(dataclasses.replace(_STORED)), speed=0.6)

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
    entity = _entity(
        OperationSettings(),
        mode=WorkMode.MODE_PAUSE,
        bp_info=1,
        responses={"cancel_job": WorkMode.MODE_READY},
    )

    await _call_start_mow(entity, areas=["switch.area_back"], speed=0.5)

    assert entity.coordinator.sent == [
        "cancel_job",
        "plan_route in MODE_READY",
        "start_job",
    ]


async def test_a_new_job_on_a_working_mower_pauses_ends_then_starts() -> None:
    """WORKING has to reach PAUSE before cancel_job, and READY before planning."""
    entity = _entity(
        OperationSettings(),
        mode=WorkMode.MODE_WORKING,
        bp_info=1,
        responses={
            "pause_execute_task": WorkMode.MODE_PAUSE,
            "cancel_job": WorkMode.MODE_READY,
        },
    )

    await _call_start_mow(entity, areas=["switch.area_back"])

    assert entity.coordinator.sent == [
        "pause_execute_task",
        "cancel_job",
        "plan_route in MODE_READY",
        "start_job",
    ]


async def test_cancel_job_on_a_working_mower_ends_the_job() -> None:
    """cancel_job used to re-read a stale WORKING after pausing and never send it."""
    entity = _entity(
        OperationSettings(),
        mode=WorkMode.MODE_WORKING,
        responses={"pause_execute_task": WorkMode.MODE_PAUSE},
    )

    await entity.async_cancel()

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
    entity = _entity(OperationSettings(), mode=WorkMode.MODE_PAUSE, bp_info=1)

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
    entity = _entity(OperationSettings(), mode=WorkMode.MODE_PAUSE, bp_info=1)

    with pytest.raises(HomeAssistantError):
        await _call_start_mow(entity, areas=["switch.area_back"])

    assert entity.coordinator.sent == ["cancel_job"]


async def test_modify_on_a_paused_mower_modifies_without_ending_the_job() -> None:
    """modify: true re-plans the job in place; it must not cancel or start one."""
    entity = _entity(OperationSettings(), mode=WorkMode.MODE_PAUSE, bp_info=1)

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
    entity = _entity(
        OperationSettings(),
        bp_info=1,
        responses={"cancel_job": WorkMode.MODE_READY},
    )

    await _call_start_mow(entity, areas=["switch.area_back"])

    assert entity.coordinator.sent == [
        "cancel_job",
        "plan_route in MODE_READY",
        "start_job",
    ]
    assert entity.coordinator.async_plan_route.await_args.args[0].areas == [222]


async def test_start_without_route_fields_resumes_a_docked_breakpoint() -> None:
    """Nothing new asked for on a docked mower holding a breakpoint: resume it."""
    entity = _entity(OperationSettings(), bp_info=1)

    await _call_start_mow(entity)

    assert entity.coordinator.sent == [
        "query_generate_route_information",
        "start_job",
    ]
