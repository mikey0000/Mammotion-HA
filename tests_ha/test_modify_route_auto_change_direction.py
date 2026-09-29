"""A route modify must carry the running job's auto-reverse setting, not the planning default.

Route writes always send the auto-reverse buffer now, so a modify built from default operation settings
would switch the setting off on the running job.
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
