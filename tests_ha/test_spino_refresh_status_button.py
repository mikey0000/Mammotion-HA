"""The Spino "Refresh status" button is a user press, sent past the cloud's offline flag.

The coordinator and ``DeviceHandle`` are real, so the offline gate is pymammotion's;
only the client is a spec'd stand-in.
"""

import pytest
from pymammotion.data.model.device import PoolCleanerDevice
from pymammotion.messaging.command_queue import Priority
from user_command_support import make_cloud_handle, make_coordinator

from custom_components.mammotion.button import SPINO_BUTTON_SENSORS
from custom_components.mammotion.coordinator import MammotionSpinoCoordinator

_DEVICE_NAME = "Spino-E1C36JT4"


def _coordinator() -> MammotionSpinoCoordinator:
    """Return a Spino coordinator whose only transport the cloud last reported offline."""
    handle = make_cloud_handle(_DEVICE_NAME, PoolCleanerDevice(), reported_offline=True)
    return make_coordinator(
        MammotionSpinoCoordinator,
        PoolCleanerDevice(),
        device_name=_DEVICE_NAME,
        handle=handle,
    )


@pytest.mark.regression
async def test_the_refresh_button_is_sent_when_the_cloud_reported_the_cleaner_offline() -> (
    None
):
    """The press queued the status request at ``Priority.NORMAL``.

    That priority honours the cloud's advisory offline flag, so pressing the
    button on a cleaner the cloud had last called offline sent nothing and
    reported nothing.
    """
    coordinator = _coordinator()
    description = next(
        entity
        for entity in SPINO_BUTTON_SENSORS
        if entity.key == "spino_refresh_status"
    )

    await description.press_fn(coordinator)

    coordinator.manager.send_command_with_args.assert_awaited_once()
    call = coordinator.manager.send_command_with_args.await_args
    assert call.args[1] == "get_report_cfg_spino"
    assert call.kwargs["priority"] is Priority.USER
