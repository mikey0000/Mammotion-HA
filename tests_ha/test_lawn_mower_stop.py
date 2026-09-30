"""The lawn_mower stop action and idle activity added in Home Assistant 2026.10.

Stop cancels the running job and leaves the mower where it is.  A mower that is
ready but off the dock has no job to resume, so it is idle rather than paused.
"""

from unittest.mock import AsyncMock, MagicMock, call

import pytest
from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntityFeature,
)
from pymammotion.data.model.device import MowingDevice
from pymammotion.messaging.command_queue import Priority
from pymammotion.utility.constant.device_constant import WorkMode

from custom_components.mammotion.lawn_mower import MammotionLawnMowerEntity


def _entity(mode: WorkMode, charge_state: int = 0) -> MammotionLawnMowerEntity:
    """Build the real lawn mower entity over a mower in the given work mode."""
    device = MowingDevice()
    device.report_data.dev.sys_status = mode
    device.report_data.dev.charge_state = charge_state
    coordinator = MagicMock()
    coordinator.unique_name = "Luba-VA123456"
    coordinator.device_name = "Luba-VA123456"
    coordinator.data = device
    coordinator.async_ensure_fresh_state = AsyncMock()
    coordinator.async_request_report_snapshot = AsyncMock()
    coordinator.async_send_command = AsyncMock()
    return MammotionLawnMowerEntity(coordinator)


@pytest.mark.parametrize(
    ("mode", "charge_state", "expected"),
    [
        (WorkMode.MODE_READY, 0, LawnMowerActivity.IDLE),
        (WorkMode.MODE_READY, 1, LawnMowerActivity.DOCKED),
        (WorkMode.MODE_PAUSE, 0, LawnMowerActivity.PAUSED),
        (WorkMode.MODE_CHARGING_PAUSE, 1, LawnMowerActivity.PAUSED),
        (WorkMode.MODE_WORKING, 0, LawnMowerActivity.MOWING),
    ],
)
def test_activity_maps_the_work_mode(
    mode: WorkMode, charge_state: int, expected: LawnMowerActivity
) -> None:
    """Ready off the dock is the only mode that moves from paused to idle."""
    assert _entity(mode, charge_state).activity == expected


def test_stop_is_a_supported_feature() -> None:
    """Without the flag, Home Assistant hides the stop action for the mower."""
    entity = _entity(WorkMode.MODE_READY)
    assert entity.supported_features & LawnMowerEntityFeature.STOP


async def test_stop_while_mowing_pauses_then_cancels_the_job() -> None:
    """The mower only accepts cancel_job once paused, and must not head home."""
    entity = _entity(WorkMode.MODE_WORKING)
    dev = entity.coordinator.data.report_data.dev

    def _paused() -> None:
        dev.sys_status = WorkMode.MODE_PAUSE

    entity.coordinator.async_request_report_snapshot.side_effect = _paused

    await entity.async_stop()

    assert entity.coordinator.async_send_command.await_args_list == [
        call("pause_execute_task", priority=Priority.USER),
        call("cancel_job", priority=Priority.USER),
    ]
