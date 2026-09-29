"""The mower's "Refresh status" button is a user press, not a background refresh.

It calls ``MammotionClient.refresh_status``, which sends on the caller's task past
the cloud's advisory offline flag, with no age check or debounce.  A failure the
library propagates is surfaced as the same translated error a user command uses.
"""

from unittest.mock import create_autospec

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.data.model.device import MowingDevice
from pymammotion.transport.base import NoTransportAvailableError
from user_command_support import make_coordinator

from custom_components.mammotion.button import BUTTON_SENSORS
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_DEVICE_NAME = "Luba-VAME9R5S"


def _description():
    return next(entity for entity in BUTTON_SENSORS if entity.key == "refresh_status")


def _coordinator() -> MammotionReportUpdateCoordinator:
    return make_coordinator(
        MammotionReportUpdateCoordinator, MowingDevice(), device_name=_DEVICE_NAME
    )


@pytest.mark.regression
async def test_the_button_press_asks_for_a_user_initiated_refresh() -> None:
    """The press called ``async_ensure_fresh_state``, a background refresh.

    That skipped a report younger than 120 s, debounced, queued at BACKGROUND and
    honoured the cloud's offline flag, so pressing it on a mower the cloud had last
    called offline sent nothing.
    """
    coordinator = create_autospec(MammotionReportUpdateCoordinator, instance=True)

    await _description().press_fn(coordinator)

    coordinator.async_refresh_status.assert_awaited_once_with()
    coordinator.async_ensure_fresh_state.assert_not_awaited()


async def test_the_refresh_goes_to_the_library_for_this_mower() -> None:
    """The coordinator delegates to the user-initiated library call, not the background one."""
    coordinator = _coordinator()

    await coordinator.async_refresh_status()

    coordinator.manager.refresh_status.assert_awaited_once_with(_DEVICE_NAME)


async def test_a_refresh_the_cloud_rejects_as_offline_is_reported() -> None:
    """The mower is marked offline and the press fails visibly, not silently."""
    coordinator = _coordinator()
    coordinator.manager.refresh_status.side_effect = DeviceOfflineException(
        29003, "iot-1"
    )

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_refresh_status()

    assert raised.value.translation_key == "command_failed"
    assert coordinator.manager.get_device_by_name.return_value.online is False


async def test_a_refresh_with_no_transport_is_reported() -> None:
    """Includes a terminally failed transport, which the library refuses up front."""
    coordinator = _coordinator()
    coordinator.manager.refresh_status.side_effect = NoTransportAvailableError("none")

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_refresh_status()

    assert raised.value.translation_key == "command_failed"
