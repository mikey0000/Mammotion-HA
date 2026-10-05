"""``start_task`` runs a stored schedule now, through ``single_schedule``.

The command is ``NavPlanTaskExecute``; nothing in the library matches a reply to
it, so the press has to succeed once the command went out.
"""

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import FailedRequestException
from pymammotion.messaging.command_queue import Priority
from pymammotion.transport.base import CommandTimeoutError
from user_command_support import make_cloud_report_coordinator

_DEVICE_NAME = "Luba-VAME9R5S"
_PLAN_ID = "1234567890"


@pytest.mark.regression
async def test_starting_a_task_succeeds_once_the_command_is_sent() -> None:
    """start_task waited for a todev_planjob_set reply the mower never sends to it.

    single_schedule sends plan_task_execute, so the wait always timed out and the
    press failed with command_unconfirmed although the mower started the task.
    """
    coordinator = make_cloud_report_coordinator(_DEVICE_NAME)
    coordinator.manager.send_command_and_wait.side_effect = CommandTimeoutError(
        "todev_planjob_set", 3
    )

    await coordinator.start_task(_PLAN_ID)

    sent = coordinator.manager.send_command_with_args.await_args
    assert sent.args == (_DEVICE_NAME, "single_schedule")
    assert sent.kwargs["priority"] is Priority.USER
    assert sent.kwargs["plan_id"] == _PLAN_ID


async def test_starting_a_task_that_was_not_delivered_is_reported() -> None:
    """A send the cloud refused fails the press with a translated error."""
    coordinator = make_cloud_report_coordinator(_DEVICE_NAME)
    exc = FailedRequestException("iot-1")
    coordinator.manager.send_command_with_args.side_effect = exc

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.start_task(_PLAN_ID)

    assert raised.value.translation_key == "command_failed"
    assert raised.value.__cause__ is exc
