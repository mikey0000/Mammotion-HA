"""Changing blade height mid-job follows the app's per-device behaviour (issue: wrong height while a task runs).

The app (``HomeMapFragment`` WorkingOptionView.onConfirm) branches on device
type: Luba 2 and newer re-issue the running route (subCmd 3) so the new height
binds to the active job, while the original Luba 1 nudges the blade motor
directly (``setKnifeHight``). An idle change is baked into the next plan, so
nothing is sent then.
"""

from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings

from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator
from custom_components.mammotion.number import LUBA_WORKING_ENTITIES

# Real names, so the Luba 1 / Luba 2+ split runs through DeviceType itself.
_LUBA1 = "Luba-AAAA"
_LUBA_PRO = "Luba-VS10001"

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

_METHODS = (
    "_is_route_job_running",
    "_seed_operation_settings_from_running_job",
    "async_modify_plan_if_mowing",
    "async_change_blade_height_if_working",
    "_apply_route_field_if_working",
    "async_modify_running_job",
    "async_change_progress_if_working",
    "async_change_speed_if_working",
    "async_change_bypass_if_working",
)


def _coordinator(
    *, device_name: str = _LUBA_PRO, running: bool = True, real_route: bool = False
) -> SimpleNamespace:
    """Bind the real coordinator methods to a fake self for one scenario.

    The coordinator's own ``__init__`` needs a hass, a config entry and a live
    device manager, none of which these branches touch — they read
    ``self.data``, ``self.device_name`` and ``self._operation_settings`` only.
    """
    device = MowingDevice()
    for field, value in _RUNNING_JOB.items():
        setattr(device.work, field, value)
    if not running:
        device.work.zone_hashs = []
    device.report_data.work.bp_hash = "123"
    device.report_data.work.area = 5

    # Stale planning defaults the user did NOT touch; only blade_height is fresh.
    settings = OperationSettings()
    settings.blade_height = 45
    settings.speed = 0.3
    settings.channel_width = 20
    settings.ultra_wave = 0
    settings.channel_mode = 0

    coordinator = SimpleNamespace(
        device_name=device_name,
        data=device,
        operation_settings=settings,
        _operation_settings=settings,
        async_modify_plan_route=AsyncMock(),
        async_blade_height=AsyncMock(),
        async_send_command=AsyncMock(),
    )
    methods = _METHODS + (
        ("async_modify_plan_route", "generate_route_information") if real_route else ()
    )
    for name in methods:
        setattr(
            coordinator,
            name,
            MethodType(getattr(MammotionBaseUpdateCoordinator, name), coordinator),
        )
    return coordinator


async def test_luba_pro_modifies_the_running_route() -> None:
    """Luba 2+ re-issues the route so the new height binds to the active job."""
    coordinator = _coordinator()
    await coordinator.async_change_blade_height_if_working()
    coordinator.async_modify_plan_route.assert_awaited_once()
    coordinator.async_blade_height.assert_not_awaited()


async def test_luba_pro_preserves_the_running_job_parameters() -> None:
    """Only the height changes; speed/spacing/detection keep the job's real values.

    Regression: the re-issued route previously took these from the planning
    defaults, clobbering the active task (issue: wrong values while a task runs).
    """
    coordinator = _coordinator()
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


async def test_luba1_sets_the_blade_motor_directly() -> None:
    """Luba 1 sends the direct height command and does not touch the route."""
    coordinator = _coordinator(device_name=_LUBA1)
    await coordinator.async_change_blade_height_if_working()
    coordinator.async_blade_height.assert_awaited_once_with(45)
    coordinator.async_modify_plan_route.assert_not_awaited()


@pytest.mark.parametrize("device_name", [_LUBA1, _LUBA_PRO])
async def test_an_idle_change_sends_nothing(device_name: str) -> None:
    """With no job running the height is baked into the next plan instead."""
    coordinator = _coordinator(device_name=device_name, running=False)
    await coordinator.async_change_blade_height_if_working()
    coordinator.async_blade_height.assert_not_awaited()
    coordinator.async_modify_plan_route.assert_not_awaited()


async def test_a_finished_job_counts_as_idle() -> None:
    """100% progress means the mow is over, whatever the breakpoint says."""
    coordinator = _coordinator()
    coordinator.data.report_data.work.area = 100 << 16
    await coordinator.async_change_blade_height_if_working()
    coordinator.async_modify_plan_route.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "field", "value"),
    [
        ("async_change_speed_if_working", "speed", 0.3),
        ("async_change_bypass_if_working", "ultra_wave", 0),
    ],
)
async def test_a_mid_job_route_field_keeps_the_rest_of_the_job(
    method: str, field: str, value: float
) -> None:
    """The edited field survives the seed; everything else comes from the job."""
    coordinator = _coordinator()
    await getattr(coordinator, method)()
    sent = coordinator.async_modify_plan_route.await_args.args[0]
    assert getattr(sent, field) == value
    assert sent.blade_height == _RUNNING_JOB["knife_height"]
    assert sent.channel_width == _RUNNING_JOB["channel_width"]
    assert sent.job_id == _RUNNING_JOB["job_id"]


@pytest.mark.parametrize(
    "method", ["async_change_speed_if_working", "async_change_bypass_if_working"]
)
async def test_luba1_sends_no_mid_job_route_change(method: str) -> None:
    """Luba 1's in-job editor only offers blade height."""
    coordinator = _coordinator(device_name=_LUBA1)
    await getattr(coordinator, method)()
    coordinator.async_modify_plan_route.assert_not_awaited()


async def test_modify_plan_if_mowing_only_fires_while_mowing() -> None:
    """The plain re-plan path keeps its own running check."""
    running = _coordinator()
    await running.async_modify_plan_if_mowing()
    running.async_modify_plan_route.assert_awaited_once()

    idle = _coordinator(running=False)
    await idle.async_modify_plan_if_mowing()
    idle.async_modify_plan_route.assert_not_awaited()


async def test_the_blade_height_number_routes_through_the_device_aware_method() -> None:
    """The number entity must not call the plain re-plan path."""
    description = next(
        entity for entity in LUBA_WORKING_ENTITIES if entity.key == "blade_height"
    )
    coordinator = _coordinator()
    coordinator.async_modify_plan_if_mowing = AsyncMock()

    description.set_fn(coordinator, 38.0)
    await description.set_async_fn(coordinator, 38.0)

    assert coordinator.operation_settings.blade_height == 38
    coordinator.async_modify_plan_route.assert_awaited_once()
    coordinator.async_modify_plan_if_mowing.assert_not_awaited()


async def test_the_shared_modify_route_keeps_the_users_speed() -> None:
    """``async_modify_plan_route`` is shared with the lawn_mower ``modify`` service.

    That caller (Priority.USER) passes the user's chosen settings, so only the
    running job's route-identity fields may be reseeded there. The full seed
    (speed, spacing, detection) belongs to the mid-job helper instead.
    """
    coordinator = _coordinator(real_route=True)
    user_settings = coordinator.operation_settings

    await coordinator.async_modify_plan_route(user_settings)

    route = coordinator.async_send_command.await_args.kwargs[
        "generate_route_information"
    ]
    assert route.speed == 0.3
    assert route.channel_width == 20
    assert route.ultra_wave == 0
    assert route.one_hashs == _RUNNING_JOB["zone_hashs"]
    # Route identity is still reseeded; subCmd 3 carries no job id on the wire,
    # so it only ever reaches the settings object.
    assert user_settings.job_id == _RUNNING_JOB["job_id"]
    assert user_settings.job_version == _RUNNING_JOB["job_ver"]
