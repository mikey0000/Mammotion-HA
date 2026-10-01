"""A direct command that did not land reaches Home Assistant as a HomeAssistantError.

Scripts mark steps ``continue_on_error: true``, and Home Assistant only honours that
for a ``HomeAssistantError``: a raw library exception stops the script, and a silent
return reports success for a command the mower never got.  Background priorities
keep their old behaviour, since nobody is waiting on them.
"""

import asyncio
from typing import Any

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import (
    DeviceUnboundException,
    FailedRequestException,
    GatewayTimeoutException,
    TooManyRequestsException,
)
from pymammotion.messaging.command_queue import Priority
from pymammotion.transport.base import (
    AccountInUseError,
    AuthError,
    BLEUnavailableError,
    CommandRejectedError,
    CommandTimeoutError,
    ConcurrentRequestError,
    TransportRateLimitedError,
)
from user_command_support import make_cloud_report_coordinator

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_DEVICE_NAME = "Luba-VAME9R5S"
_DIRECT = (
    pytest.param(Priority.USER, id="user"),
    pytest.param(Priority.EMERGENCY, id="emergency"),
)


def _coordinator(
    *, cloud_usable: bool = True, reported_offline: bool = False
) -> MammotionReportUpdateCoordinator:
    return make_cloud_report_coordinator(
        _DEVICE_NAME, cloud_usable=cloud_usable, reported_offline=reported_offline
    )


async def _send_command(
    coordinator: MammotionReportUpdateCoordinator, priority: Priority
) -> Any:
    return await coordinator.async_send_command("return_to_dock", priority=priority)


async def _send_and_wait(
    coordinator: MammotionReportUpdateCoordinator, priority: Priority
) -> Any:
    return await coordinator.async_send_and_wait(
        "start_job", "zone_start_precent_t", priority=priority
    )


def _fail_with(coordinator: MammotionReportUpdateCoordinator, exc: Exception) -> None:
    coordinator.manager.send_command_with_args.side_effect = exc
    coordinator.manager.send_command_and_wait.side_effect = exc


def _server_disconnected() -> aiohttp.ServerDisconnectedError:
    return aiohttp.ServerDisconnectedError()


def _html_error_page() -> aiohttp.ContentTypeError:
    """Return what aiohttp raises when a 5xx answers with an HTML page, not JSON."""
    return aiohttp.ContentTypeError(None, (), status=502, message="text/html")


_SENDERS = pytest.mark.parametrize(
    "send", [_send_command, _send_and_wait], ids=["send_command", "send_and_wait"]
)


@pytest.mark.regression
@pytest.mark.parametrize("priority", _DIRECT)
@_SENDERS
async def test_a_direct_command_with_no_transport_is_reported(
    send: Any, priority: Priority
) -> None:
    """The pre-send check swallowed ``NoTransportAvailableError`` and returned.

    So a dock press with no usable transport finished as a successful service call
    without anything being sent.
    """
    coordinator = _coordinator(cloud_usable=False)

    with pytest.raises(HomeAssistantError) as raised:
        await send(coordinator, priority)

    assert raised.value.translation_key == "command_failed"
    coordinator.manager.send_command_with_args.assert_not_awaited()
    coordinator.manager.send_command_and_wait.assert_not_awaited()


@pytest.mark.regression
@pytest.mark.parametrize("priority", _DIRECT)
@pytest.mark.parametrize(
    "exc",
    [
        FailedRequestException("iot-1"),
        GatewayTimeoutException("gateway timeout", "iot-1"),
    ],
    ids=["failed_request", "gateway_timeout"],
)
async def test_a_direct_send_the_cloud_did_not_deliver_is_reported(
    exc: Exception, priority: Priority
) -> None:
    """``async_send_command`` counted a failure or logged, then returned as if sent."""
    coordinator = _coordinator()
    _fail_with(coordinator, exc)

    with pytest.raises(HomeAssistantError) as raised:
        await _send_command(coordinator, priority)

    assert raised.value.translation_key == "command_failed"
    assert raised.value.__cause__ is exc


@pytest.mark.regression
@pytest.mark.parametrize("priority", _DIRECT)
@pytest.mark.parametrize(
    "exc",
    [
        GatewayTimeoutException("gateway timeout", "iot-1"),
        ConcurrentRequestError("zone_start_precent_t already pending"),
    ],
    ids=["gateway_timeout", "concurrent_request"],
)
async def test_a_direct_send_and_wait_that_was_not_delivered_is_reported(
    exc: Exception, priority: Priority
) -> None:
    """``_async_device_call`` ended these in ``pass`` and returned None."""
    coordinator = _coordinator()
    _fail_with(coordinator, exc)

    with pytest.raises(HomeAssistantError) as raised:
        await _send_and_wait(coordinator, priority)

    assert raised.value.translation_key == "command_failed"


@pytest.mark.regression
@pytest.mark.parametrize("priority", _DIRECT)
async def test_a_direct_send_and_wait_without_a_reply_says_it_is_unconfirmed(
    priority: Priority,
) -> None:
    """A missing reply was swallowed; the command may still have landed, so it says so."""
    coordinator = _coordinator()
    _fail_with(coordinator, CommandTimeoutError("zone_start_precent_t", 3))

    with pytest.raises(HomeAssistantError) as raised:
        await _send_and_wait(coordinator, priority)

    assert raised.value.translation_key == "command_unconfirmed"


@pytest.mark.regression
@pytest.mark.parametrize("priority", _DIRECT)
@pytest.mark.parametrize(
    "make_exc",
    [
        _server_disconnected,
        _html_error_page,
        aiohttp.ConnectionTimeoutError,
        lambda: DeviceUnboundException("device is unbind", "iot-1"),
        lambda: FailedRequestException("iot-1"),
        lambda: CommandRejectedError("RES_FAILURE"),
        lambda: BLEUnavailableError("no proxy reached the mower"),
        TimeoutError,
    ],
    ids=[
        "server_disconnected",
        "content_type",
        "connection_timeout",
        "device_unbound",
        "failed_request",
        "command_rejected",
        "ble_unavailable",
        "timeout",
    ],
)
@_SENDERS
async def test_no_failure_of_a_direct_command_escapes_as_a_raw_exception(
    send: Any, make_exc: Any, priority: Priority
) -> None:
    """These left the action as a raw exception, which ``continue_on_error`` ignores."""
    coordinator = _coordinator()
    exc = make_exc()
    _fail_with(coordinator, exc)

    with pytest.raises(HomeAssistantError) as raised:
        await send(coordinator, priority)

    assert raised.value.translation_key == "command_failed"
    assert raised.value.__cause__ is exc


@pytest.mark.parametrize("priority", _DIRECT)
@pytest.mark.parametrize(
    "exc",
    [
        TransportRateLimitedError("banned for 12 h"),
        TooManyRequestsException("HTTP 429", "iot-1"),
    ],
    ids=["ban", "http_429"],
)
@_SENDERS
async def test_a_direct_command_refused_by_the_rate_limit_says_so(
    send: Any, exc: Exception, priority: Priority
) -> None:
    """Once the library lets the ban through, it surfaces as the rate-limit message."""
    coordinator = _coordinator()
    _fail_with(coordinator, exc)

    with pytest.raises(HomeAssistantError) as raised:
        await send(coordinator, priority)

    assert raised.value.translation_key == "api_limit_exceeded"


@pytest.mark.parametrize(
    "exc",
    [
        FailedRequestException("iot-1"),
        GatewayTimeoutException("gateway timeout", "iot-1"),
    ],
    ids=["failed_request", "gateway_timeout"],
)
async def test_a_background_send_the_cloud_did_not_deliver_stays_quiet(
    exc: Exception,
) -> None:
    """Nobody waits on a background send, so it still only counts and returns."""
    coordinator = _coordinator()
    _fail_with(coordinator, exc)

    assert await _send_command(coordinator, Priority.NORMAL) is False
    coordinator.manager.send_command_with_args.assert_awaited_once()


@pytest.mark.parametrize(
    "exc",
    [
        GatewayTimeoutException("gateway timeout", "iot-1"),
        CommandTimeoutError("zone_start_precent_t", 3),
        ConcurrentRequestError("zone_start_precent_t already pending"),
    ],
    ids=["gateway_timeout", "command_timeout", "concurrent_request"],
)
async def test_a_background_send_and_wait_without_delivery_stays_quiet(
    exc: Exception,
) -> None:
    """The poll retries on its own, so these still end quietly, reporting no reply."""
    coordinator = _coordinator()
    _fail_with(coordinator, exc)

    assert await _send_and_wait(coordinator, Priority.NORMAL) is False
    coordinator.manager.send_command_and_wait.assert_awaited_once()


@pytest.mark.regression
@pytest.mark.parametrize(
    "priority", [Priority.NORMAL, Priority.BACKGROUND], ids=["normal", "background"]
)
@pytest.mark.parametrize(
    "make_exc",
    [
        _server_disconnected,
        lambda: DeviceUnboundException("device is unbind", "iot-1"),
        lambda: FailedRequestException("iot-1"),
        lambda: CommandRejectedError("RES_FAILURE"),
        lambda: BLEUnavailableError("no proxy reached the mower"),
        TimeoutError,
        lambda: TransportRateLimitedError("banned for 12 h"),
        lambda: TooManyRequestsException("HTTP 429", "iot-1"),
    ],
    ids=[
        "server_disconnected",
        "device_unbound",
        "failed_request",
        "command_rejected",
        "ble_unavailable",
        "timeout",
        "ban",
        "http_429",
    ],
)
@_SENDERS
async def test_a_background_send_never_raises(
    send: Any, make_exc: Any, priority: Priority
) -> None:
    """The helpers disagreed: one swallowed what the other re-raised raw.

    ``async_send_command`` swallowed ``FailedRequestException`` while
    ``_async_device_call`` re-raised it; both re-raised the rest raw, and a rate
    limit raised ``api_limit_exceeded`` on any priority, so a poll could fail
    the caller that nobody was waiting on.
    """
    coordinator = _coordinator()
    _fail_with(coordinator, make_exc())

    assert await send(coordinator, priority) is False


def _credentials_rejected() -> AuthError:
    return AuthError("access token rejected")


def _no_ble_slot() -> asyncio.CancelledError:
    """Return what bleak_retry_connector raises when no proxy has a free slot."""
    return asyncio.CancelledError()


_UNDELIVERED = pytest.mark.parametrize(
    "make_exc",
    [_credentials_rejected, _no_ble_slot],
    ids=["credentials_rejected", "no_ble_slot"],
)


@pytest.mark.regression
@pytest.mark.parametrize("priority", _DIRECT)
@_UNDELIVERED
@_SENDERS
async def test_a_direct_command_lost_to_credentials_or_ble_slots_is_reported(
    send: Any, make_exc: Any, priority: Priority
) -> None:
    """Both were handled (refresh, or "skipping") and then returned as if sent."""
    coordinator = _coordinator()
    _fail_with(coordinator, make_exc())

    with pytest.raises(HomeAssistantError) as raised:
        await send(coordinator, priority)

    assert raised.value.translation_key == "command_failed"


@_UNDELIVERED
@_SENDERS
async def test_a_background_command_lost_to_credentials_or_ble_slots_stays_quiet(
    send: Any, make_exc: Any
) -> None:
    """The poll retries on its own, so these still end without raising."""
    coordinator = _coordinator()
    _fail_with(coordinator, make_exc())

    assert not await send(coordinator, Priority.NORMAL)


@pytest.mark.parametrize("priority", _DIRECT)
@_SENDERS
async def test_a_direct_command_refused_by_the_account_lock_says_so(
    send: Any, priority: Priority
) -> None:
    """The library's gate refuses it while the Mammotion app holds the account."""
    coordinator = make_cloud_report_coordinator(_DEVICE_NAME, account_in_use=True)

    with pytest.raises(HomeAssistantError) as raised:
        await send(coordinator, priority)

    assert raised.value.translation_key == "account_in_use"


@pytest.mark.parametrize("priority", _DIRECT)
@_SENDERS
async def test_a_direct_send_the_account_lock_refused_says_so(
    send: Any, priority: Priority
) -> None:
    """The lock can be taken between the gate and the send; the send says so too."""
    coordinator = _coordinator()
    _fail_with(coordinator, AccountInUseError("in use by another session"))

    with pytest.raises(HomeAssistantError) as raised:
        await send(coordinator, priority)

    assert raised.value.translation_key == "account_in_use"


@_SENDERS
async def test_a_background_command_held_by_the_account_lock_stays_quiet(
    send: Any,
) -> None:
    """Nobody waits on a poll, so the lock only drops it."""
    coordinator = make_cloud_report_coordinator(_DEVICE_NAME, account_in_use=True)

    assert await send(coordinator, Priority.NORMAL) is False
