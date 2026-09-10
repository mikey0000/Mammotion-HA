"""Changing blade height mid-job follows the app's per-device behaviour (issue: wrong height while a task runs).

The app (``HomeMapFragment`` WorkingOptionView.onConfirm) branches on device
type: Luba 2 and newer re-issue the running route (subCmd 3) so the new height
binds to the active job, while the original Luba 1 nudges the blade motor
directly (``setKnifeHight``). An idle change is baked into the next plan, so
nothing is sent then.
"""

import ast
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_METHODS = (
    "_is_route_job_running",
    "_seed_operation_settings_from_running_job",
    "async_modify_plan_if_mowing",
    "async_change_blade_height_if_working",
    "_apply_route_field_if_working",
    "async_change_speed_if_working",
    "async_change_bypass_if_working",
)

# Values the running job carries; deliberately different from the stale
# planning defaults below so a leak of the defaults is visible.
_RUNNING_JOB = {
    "zone_hashs": [123],
    "toward": 10,
    "toward_mode": 1,
    "toward_included_angle": 20,
    "edge_mode": 2,
    "job_mode": 3,
    "job_id": 99,
    "job_ver": 7,
    "speed": 0.4,
    "channel_width": 30,
    "ultra_wave": 2,
    "channel_mode": 1,
    "knife_height": 60,
    "auto_change_direction": 1,
}


def _bound_coordinator(*, is_luba_pro: bool, running: bool) -> types.SimpleNamespace:
    """Execute the real coordinator methods against a fake self for one scenario."""
    src = (_ROOT / "coordinator.py").read_text()
    tree = ast.parse(src)
    segments = {
        node.name: ast.get_source_segment(src, node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name in _METHODS
    }
    assert set(segments) == set(_METHODS), segments.keys()

    class _DeviceType:
        @staticmethod
        def is_luba_pro(_name: str) -> bool:
            return is_luba_pro

    namespace: dict = {
        "cast": lambda _t, value: value,
        "DeviceType": _DeviceType,
        "MowingDevice": object,
    }
    for name in _METHODS:
        exec(compile(segments[name], "<coordinator>", "exec"), namespace)  # noqa: S102

    work = types.SimpleNamespace(
        **{**_RUNNING_JOB, "zone_hashs": [123] if running else []}
    )
    report_work = types.SimpleNamespace(bp_hash="123", area=5)
    # Stale planning defaults the user did NOT touch; only blade_height is fresh.
    operation_settings = types.SimpleNamespace(
        blade_height=45,
        speed=0.3,
        channel_width=20,
        ultra_wave=0,
        channel_mode=0,
    )
    coordinator = types.SimpleNamespace(
        device_name="Luba-XXXX",
        data=types.SimpleNamespace(
            work=work,
            report_data=types.SimpleNamespace(work=report_work),
        ),
        operation_settings=operation_settings,
        _operation_settings=operation_settings,
        async_modify_plan_route=AsyncMock(),
        async_blade_height=AsyncMock(),
    )
    for name in _METHODS:
        setattr(coordinator, name, types.MethodType(namespace[name], coordinator))
    return coordinator


@pytest.mark.anyio
async def test_luba_pro_modifies_the_running_route() -> None:
    """Luba 2+ re-issues the route so the new height binds to the active job."""
    coordinator = _bound_coordinator(is_luba_pro=True, running=True)
    await coordinator.async_change_blade_height_if_working()
    coordinator.async_modify_plan_route.assert_awaited_once()
    coordinator.async_blade_height.assert_not_awaited()


@pytest.mark.anyio
async def test_luba_pro_preserves_the_running_job_parameters() -> None:
    """Only the height changes; speed/spacing/detection keep the job's real values.

    Regression: the re-issued route previously took these from the planning
    defaults, clobbering the active task (issue: wrong values while a task runs).
    """
    coordinator = _bound_coordinator(is_luba_pro=True, running=True)
    await coordinator.async_change_blade_height_if_working()
    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert sent.blade_height == 45  # the user's new height wins over the job's 60
    assert sent.speed == _RUNNING_JOB["speed"]
    assert sent.channel_width == _RUNNING_JOB["channel_width"]
    assert sent.ultra_wave == _RUNNING_JOB["ultra_wave"]
    assert sent.channel_mode == _RUNNING_JOB["channel_mode"]
    assert sent.areas == _RUNNING_JOB["zone_hashs"]
    assert sent.job_id == _RUNNING_JOB["job_id"]
    assert sent.job_version == _RUNNING_JOB["job_ver"]


@pytest.mark.anyio
async def test_luba1_sets_the_blade_motor_directly() -> None:
    """Luba 1 sends the direct height command and does not touch the route."""
    coordinator = _bound_coordinator(is_luba_pro=False, running=True)
    await coordinator.async_change_blade_height_if_working()
    coordinator.async_blade_height.assert_awaited_once_with(45)
    coordinator.async_modify_plan_route.assert_not_awaited()
    assert coordinator.operation_settings.speed == 0.3  # unseeded, left as-is


@pytest.mark.anyio
async def test_idle_change_sends_nothing() -> None:
    """With no job running the height is only kept for the next plan."""
    for is_luba_pro in (True, False):
        coordinator = _bound_coordinator(is_luba_pro=is_luba_pro, running=False)
        await coordinator.async_change_blade_height_if_working()
        coordinator.async_modify_plan_route.assert_not_awaited()
        coordinator.async_blade_height.assert_not_awaited()
        assert coordinator.operation_settings.speed == 0.3  # nothing seeded


@pytest.mark.anyio
async def test_luba_pro_speed_change_preserves_the_running_job() -> None:
    """A mid-job speed change re-issues the route with only speed changed (issue follow-up)."""
    coordinator = _bound_coordinator(is_luba_pro=True, running=True)
    coordinator.operation_settings.speed = 0.55  # the user's new speed (stored by set_fn)
    await coordinator.async_change_speed_if_working()
    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert sent.speed == 0.55  # user's value wins over the job's 0.4
    assert sent.channel_width == _RUNNING_JOB["channel_width"]
    assert sent.ultra_wave == _RUNNING_JOB["ultra_wave"]
    assert sent.channel_mode == _RUNNING_JOB["channel_mode"]
    assert sent.blade_height == _RUNNING_JOB["knife_height"]  # untouched -> job's real height


@pytest.mark.anyio
async def test_luba_pro_bypass_change_preserves_the_running_job() -> None:
    """A mid-job detection change re-issues the route with only ultra_wave changed."""
    coordinator = _bound_coordinator(is_luba_pro=True, running=True)
    coordinator.operation_settings.ultra_wave = 11  # the user's new detection mode
    await coordinator.async_change_bypass_if_working()
    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert sent.ultra_wave == 11  # user's value wins over the job's 2
    assert sent.speed == _RUNNING_JOB["speed"]
    assert sent.channel_width == _RUNNING_JOB["channel_width"]
    assert sent.blade_height == _RUNNING_JOB["knife_height"]


@pytest.mark.anyio
async def test_luba1_speed_and_bypass_send_nothing_mid_job() -> None:
    """Luba 1's in-job editor only changes height; speed/detection send nothing."""
    coordinator = _bound_coordinator(is_luba_pro=False, running=True)
    coordinator.operation_settings.speed = 0.55
    coordinator.operation_settings.ultra_wave = 11
    await coordinator.async_change_speed_if_working()
    await coordinator.async_change_bypass_if_working()
    coordinator.async_modify_plan_route.assert_not_awaited()
    coordinator.async_blade_height.assert_not_awaited()


@pytest.mark.anyio
async def test_idle_speed_and_bypass_send_nothing() -> None:
    """With no job running, speed/detection are only kept for the next plan."""
    coordinator = _bound_coordinator(is_luba_pro=True, running=False)
    await coordinator.async_change_speed_if_working()
    await coordinator.async_change_bypass_if_working()
    coordinator.async_modify_plan_route.assert_not_awaited()


def test_number_entity_wires_the_blade_height_branch() -> None:
    """The blade_height number routes through the device-type aware method."""
    src = (_ROOT / "number.py").read_text()
    start = src.index('key="blade_height"')
    entity = src[start : src.index("),\n", src.index("get_fn", start))]
    assert "async_change_blade_height_if_working()" in entity
    assert "async_modify_plan_if_mowing()" not in entity


def test_shared_modify_route_does_not_seed_speed_from_the_job() -> None:
    """The start/modify-service path must keep speed/spacing user-overridable.

    ``async_modify_plan_route`` is shared with the lawn_mower ``modify`` service
    (Priority.USER), which passes the user's chosen settings; only the running
    job's route-identity fields may be reseeded there. The full-parameter seed
    (speed, channel_width, ultra_wave, channel_mode) lives in the dedicated
    helper instead.
    """
    src = (_ROOT / "coordinator.py").read_text()
    body = _function(src, "async_modify_plan_route")
    fields = (
        "work.speed",
        "work.channel_width",
        "work.ultra_wave",
        "work.channel_mode",
    )
    for leaked in fields:
        assert leaked not in body, leaked
    helper = _function(src, "_seed_operation_settings_from_running_job")
    for seeded in fields:
        assert seeded in helper, seeded


def _function(src: str, name: str) -> str:
    """Return the source text of the named function."""
    tree = ast.parse(src)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )
    return ast.get_source_segment(src, node)
