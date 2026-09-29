"""The coordinator builds routes with pymammotion's ``build_route_information``, not a copy of it.

The integration used to carry its own copy of the route builder; it had drifted
from the library's by one rule (a modify with ``toward_mode`` 0 must send
``toward`` 0), and it raised on any coordinator whose data is not a mower.
"""

from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import betterproto2
from pymammotion.data.model import GenerateRouteInformation
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.data.model.report_info import Maintain
from pymammotion.mammotion.commands.mammotion_command import MammotionCommand
from pymammotion.proto import LubaMsg

from custom_components.mammotion import coordinator as coordinator_module
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator

_LUBA2 = "Luba-VA6ABCDE"
_USER_ACCOUNT = 1


def _coordinator(data: object) -> SimpleNamespace:
    """Bind the real route methods to a fake self carrying only what they read."""
    coordinator = SimpleNamespace(
        device_name=_LUBA2, data=data, async_send_command=AsyncMock()
    )
    for name in ("async_modify_plan_route", "generate_route_information"):
        setattr(
            coordinator,
            name,
            MethodType(getattr(MammotionBaseUpdateCoordinator, name), coordinator),
        )
    return coordinator


def _running_job(*, toward: int, toward_mode: int) -> MowingDevice:
    device = MowingDevice()
    device.work.zone_hashs = [123]
    device.work.toward = toward
    device.work.toward_mode = toward_mode
    return device


async def _sent_toward(device: MowingDevice) -> int:
    """Modify the running job and return ``toward`` as the real wire builder encodes it."""
    coordinator = _coordinator(device)

    await coordinator.async_modify_plan_route(OperationSettings())

    route = coordinator.async_send_command.await_args.kwargs[
        "generate_route_information"
    ]
    payload = MammotionCommand(_LUBA2, _USER_ACCOUNT).modify_route_information(route)
    name, cover_path = betterproto2.which_one_of(
        LubaMsg().parse(payload).nav, "SubNavMsg"
    )
    assert name == "bidire_reqconver_path"
    return cover_path.toward


async def test_modifying_a_job_with_toward_mode_zero_sends_toward_zero() -> None:
    """The integration's copy of the builder re-sent the job's stale angle; the app sends 0."""
    assert await _sent_toward(_running_job(toward=45, toward_mode=0)) == 0


async def test_modifying_a_job_with_a_toward_mode_keeps_its_angle() -> None:
    """Only toward_mode 0 zeroes the angle; a set mode sends the job's own."""
    assert await _sent_toward(_running_job(toward=45, toward_mode=1)) == 45


def test_the_route_comes_from_the_library_builder() -> None:
    """The call is the contract: the coordinator hands its name and data to pymammotion."""
    device = MowingDevice()
    settings = OperationSettings()
    built = GenerateRouteInformation(toward=7)

    with patch.object(
        coordinator_module, "build_route_information", return_value=built
    ) as build:
        result = _coordinator(device).generate_route_information(settings)

    build.assert_called_once_with(_LUBA2, device, settings)
    assert result is built


def test_a_coordinator_without_mower_data_still_builds_a_route() -> None:
    """The integration's copy read ``self.data.report_data`` unguarded and raised on non-mower data."""
    settings = OperationSettings(is_dump=True, areas=[1])

    route = _coordinator(Maintain()).generate_route_information(settings)

    assert route.one_hashs == [1]
    assert settings.is_dump is True
