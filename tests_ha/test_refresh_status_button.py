"""The mower's "Refresh status" button is a user press, not a background refresh.

It calls ``MammotionClient.refresh_status``, which sends on the caller's task past
the cloud's advisory offline flag, with no age check or debounce.  A failure the
library propagates is surfaced as the same translated error a user command uses.
"""

from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import create_autospec

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import (
    DeviceOfflineException,
    FailedRequestException,
    GatewayTimeoutException,
)
from pymammotion.data.model.device import MowingDevice
from pymammotion.transport.base import (
    AuthError,
    CommandRejectedError,
    NoTransportAvailableError,
)
from user_command_support import make_cloud_handle, make_coordinator

from custom_components.mammotion.button import BUTTON_SENSORS
from custom_components.mammotion.const import CONF_HAS_CLOUD_ACCOUNT
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_DEVICE_NAME = "Luba-VAME9R5S"


def _description():
    return next(entity for entity in BUTTON_SENSORS if entity.key == "refresh_status")


def _coordinator() -> MammotionReportUpdateCoordinator:
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, MowingDevice(), device_name=_DEVICE_NAME
    )
    # No cloud account, so a credential failure skips the real login refresh.
    coordinator.config_entry = SimpleNamespace(
        options={}, data={CONF_HAS_CLOUD_ACCOUNT: False}
    )
    return coordinator


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


@pytest.mark.regression
@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: GatewayTimeoutException("gateway timeout", "iot-1"),
        lambda: AuthError("access token rejected"),
        lambda: FailedRequestException("iot-1"),
        lambda: CommandRejectedError("RES_FAILURE"),
        aiohttp.ServerDisconnectedError,
    ],
    ids=[
        "gateway_timeout",
        "credentials_rejected",
        "failed_request",
        "command_rejected",
        "server_disconnected",
    ],
)
async def test_every_refresh_that_did_not_land_is_reported(
    make_exc: Callable[[], Exception],
) -> None:
    """The refresh had its own exception mapping beside the shared one.

    A gateway timeout was logged and a credential failure refreshed the login,
    both then reporting the press as a success; the rest escaped as raw library
    exceptions, which ``continue_on_error`` does not honour.
    """
    coordinator = _coordinator()
    exc = make_exc()
    coordinator.manager.refresh_status.side_effect = exc

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_refresh_status()

    assert raised.value.translation_key == "command_failed"
    assert raised.value.__cause__ is exc


@pytest.mark.regression
async def test_a_refresh_with_nothing_to_carry_it_is_not_sent() -> None:
    """It skipped the shared pre-send check and called the library regardless."""
    coordinator = _coordinator()
    coordinator.manager.mower.return_value = make_cloud_handle(
        _DEVICE_NAME, MowingDevice(), reported_offline=False, cloud_usable=False
    )

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_refresh_status()

    assert raised.value.translation_key == "command_failed"
    coordinator.manager.refresh_status.assert_not_awaited()
