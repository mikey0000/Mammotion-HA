"""A route modify must carry the running job's auto-reverse setting, not the planning default.

Route writes always send the auto-reverse buffer now, so a modify built from default operation settings
would switch the setting off on the running job.  The one exception is the user changing it on
the running job, which has to win.
"""

from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings

from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator

_FIRMWARE = "2.3.30.39"


def _running_job_coordinator(*, reported: bool | None) -> SimpleNamespace:
    """Bind the real modify methods to a fake self over a running job on new firmware."""
    device = MowingDevice()
    device.device_firmwares.device_version = _FIRMWARE
    device.work.zone_hashs = [123]
    device.work.edge_mode = 1
    device.work.auto_change_direction = reported
    device.report_data.work.bp_hash = "123"
    settings = OperationSettings()
    coordinator = SimpleNamespace(
        device_name="Luba-VA123456",
        data=device,
        operation_settings=settings,
        _operation_settings=settings,
        async_send_command=AsyncMock(),
    )
    for name in ("async_modify_plan_route", "generate_route_information"):
        setattr(
            coordinator,
            name,
            MethodType(getattr(MammotionBaseUpdateCoordinator, name), coordinator),
        )
    return coordinator


@pytest.mark.parametrize(("reported", "expected"), [(True, 1), (False, 0)])
async def test_modifying_the_running_route_keeps_its_auto_reverse_setting(
    reported: bool, expected: int
) -> None:
    """The running job's setting wins over a default that would otherwise turn it off."""
    coordinator = _running_job_coordinator(reported=reported)
    coordinator.operation_settings.auto_change_direction = 1 - expected

    await coordinator.async_modify_plan_route(coordinator.operation_settings)

    route = coordinator.async_send_command.await_args.kwargs[
        "generate_route_information"
    ]
    assert route.auto_change_direction == expected


async def test_a_job_that_reports_no_auto_reverse_keeps_the_users_choice() -> None:
    """``None`` means the mower did not report it, so the setting the user chose is used."""
    coordinator = _running_job_coordinator(reported=None)
    coordinator.operation_settings.auto_change_direction = 1

    await coordinator.async_modify_plan_route(coordinator.operation_settings)

    route = coordinator.async_send_command.await_args.kwargs[
        "generate_route_information"
    ]
    assert route.auto_change_direction == 1


def _job_service_coordinator(*, reported: bool) -> SimpleNamespace:
    """Return a running job with the real ``modify_running_job`` path bound as well."""
    coordinator = _running_job_coordinator(reported=reported)
    for name in (
        "_is_route_job_running",
        "_running_job_fields",
        "_seed_operation_settings_from_running_job",
        "_running_job_refusal",
        "async_modify_running_job",
    ):
        setattr(
            coordinator,
            name,
            MethodType(getattr(MammotionBaseUpdateCoordinator, name), coordinator),
        )
    return coordinator


@pytest.mark.regression
@pytest.mark.parametrize(("reported", "chosen"), [(True, 0), (False, 1)])
async def test_changing_auto_reverse_on_the_running_job_reaches_the_route(
    reported: bool, chosen: int
) -> None:
    """The service took the field, then the route was rebuilt with the job's old value."""
    coordinator = _job_service_coordinator(reported=reported)

    await coordinator.async_modify_running_job(auto_change_direction=chosen)

    route = coordinator.async_send_command.await_args.kwargs[
        "generate_route_information"
    ]
    assert route.auto_change_direction == chosen


@pytest.mark.parametrize(("reported", "expected"), [(True, 1), (False, 0)])
async def test_changing_another_setting_keeps_the_jobs_auto_reverse(
    reported: bool, expected: int
) -> None:
    """Only a choice the user made overrides the running job's own setting."""
    coordinator = _job_service_coordinator(reported=reported)

    await coordinator.async_modify_running_job(speed=0.5)

    route = coordinator.async_send_command.await_args.kwargs[
        "generate_route_information"
    ]
    assert route.auto_change_direction == expected
