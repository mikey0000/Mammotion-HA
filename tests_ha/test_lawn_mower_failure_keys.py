"""A lawn-mower action that failed says which action it was.

The coordinator maps every send failure to a HomeAssistantError, so the entity's
own ``except COMMAND_EXCEPTIONS`` clauses (dock_failed, pause_failed, …) no longer
saw them and every failure read as the generic "Failed to send command".  The
entity now names its action to the coordinator, which keeps the one mapping.
The coordinator and entity are the shipped classes over a spec'd client.
"""

from typing import Any

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.transport.base import CommandTimeoutError, TransportRateLimitedError
from pymammotion.utility.constant.device_constant import WorkMode
from user_command_support import make_coordinator

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.lawn_mower import MammotionLawnMowerEntity

_MOWER = "Luba-VS123456"


def _entity(
    mode: WorkMode, exc: Exception, *, charge_state: int = 0
) -> MammotionLawnMowerEntity:
    """Return the lawn mower over a mower in *mode* whose every send fails with *exc*."""
    device = MowingDevice()
    device.report_data.dev.sys_status = mode
    device.report_data.dev.charge_state = charge_state
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=_MOWER
    )
    coordinator.unique_name = _MOWER
    coordinator._operation_settings = OperationSettings()  # noqa: SLF001
    coordinator.manager.send_command_with_args.side_effect = exc
    coordinator.manager.send_command_and_wait.side_effect = exc
    return MammotionLawnMowerEntity(coordinator)


def _offline() -> DeviceOfflineException:
    return DeviceOfflineException(29003, "iot-1")


async def _dock(entity: MammotionLawnMowerEntity) -> None:
    await entity.async_dock()


async def _pause(entity: MammotionLawnMowerEntity) -> None:
    await entity.async_pause()


async def _start(entity: MammotionLawnMowerEntity) -> None:
    await entity.async_start_mowing()


@pytest.mark.regression
@pytest.mark.parametrize(
    ("action", "mode", "key"),
    [
        (_dock, WorkMode.MODE_READY, "dock_failed"),
        (_dock, WorkMode.MODE_RETURNING, "dock_cancel_failed"),
        (_dock, WorkMode.MODE_WORKING, "pause_failed"),
        (_pause, WorkMode.MODE_WORKING, "pause_failed"),
        (_pause, WorkMode.MODE_RETURNING, "dock_cancel_failed"),
        (_start, WorkMode.MODE_READY, "start_failed"),
        (_start, WorkMode.MODE_RETURNING, "dock_cancel_failed"),
    ],
    ids=[
        "dock",
        "dock while returning",
        "dock while mowing",
        "pause",
        "pause while returning",
        "start",
        "start while returning",
    ],
)
async def test_a_failed_action_names_the_action(
    action: Any, mode: WorkMode, key: str
) -> None:
    """Every failure surfaced as ``command_failed`` once the coordinator mapped it first."""
    entity = _entity(mode, _offline())

    with pytest.raises(HomeAssistantError) as raised:
        await action(entity)

    assert raised.value.translation_key == key


@pytest.mark.parametrize(
    ("exc", "key"),
    [
        (TransportRateLimitedError("banned for 12 h"), "api_limit_exceeded"),
        (CommandTimeoutError("todev_taskctrl_ack", 3), "command_unconfirmed"),
    ],
    ids=["rate_limited", "unconfirmed"],
)
async def test_the_rate_limit_and_unconfirmed_messages_are_kept(
    exc: Exception, key: str
) -> None:
    """Those say something the action name would hide, so they are not replaced."""
    entity = _entity(WorkMode.MODE_RETURNING, exc)

    with pytest.raises(HomeAssistantError) as raised:
        await _start(entity)

    assert raised.value.translation_key == key
