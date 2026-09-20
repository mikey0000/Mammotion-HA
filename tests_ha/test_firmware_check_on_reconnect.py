"""A device that reappears after missing its slot gets checked straight away.

The version coordinator polls weekly (``DEVICE_VERSION_INTERVAL``).  A mower
that was offline when its turn came round used to wait out another whole
interval, so a machine that is only powered up at weekends could go a month
without anyone asking the cloud whether there is new firmware for it.

There is deliberately no online/offline edge here.  ``coordinator.device`` is
the Aliyun account record, one object shared by all five of a mower's
coordinators, and nothing ever sets ``online = False`` on it — only the state
model behind ``manager.get_device_by_name`` is marked offline.  An edge built
on it would fire once per config-entry load at best.  The week-old test is the
rate limiter instead, so these tests drive ``_on_state_changed`` against a
*shared* record that is already online, which is what production looks like.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util
from pymammotion.data.model.device import MowingDevice

from custom_components.mammotion.coordinator import (
    DEVICE_VERSION_INTERVAL,
    FIRMWARE_CHECK_RETRY_INTERVAL,
    MammotionBaseUpdateCoordinator,
    MammotionDeviceVersionUpdateCoordinator,
)

_DEVICE_NAME = "Luba-VS123456"


def _coordinator(
    *, last_check_age: timedelta | None, cloud: bool = True
) -> MammotionDeviceVersionUpdateCoordinator:
    """Build the real version coordinator over a store with a known check time."""
    coordinator = MammotionDeviceVersionUpdateCoordinator.__new__(
        MammotionDeviceVersionUpdateCoordinator
    )
    device = MowingDevice()
    coordinator.data = device
    # The account record another coordinator has already marked online, as it
    # would be by the time this one sees the same push.
    coordinator.device = SimpleNamespace(online=True)
    coordinator.device_name = _DEVICE_NAME
    coordinator._firmware_check_attempted = None

    store = MagicMock()
    store.firmware_checked_at.return_value = (
        None if last_check_age is None else dt_util.utcnow() - last_check_age
    )
    coordinator._store = store

    coordinator.manager = MagicMock()
    coordinator.manager.mammotion_http = MagicMock() if cloud else None
    coordinator.manager.reauth_required = None
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


def _snapshot(device: MowingDevice) -> MagicMock:
    """Return the state-changed push the handle emits when a device reports in."""
    return MagicMock(raw=device)


@pytest.mark.parametrize(
    "age",
    [None, DEVICE_VERSION_INTERVAL, DEVICE_VERSION_INTERVAL + timedelta(days=21)],
    ids=["never-checked", "exactly-a-week", "long-overdue"],
)
async def test_contact_with_a_stale_check_refreshes_now(
    age: timedelta | None,
) -> None:
    """The reported case: a week or more since anyone asked the cloud."""
    coordinator = _coordinator(last_check_age=age)

    await coordinator._on_state_changed(_snapshot(coordinator.data))

    coordinator.async_request_refresh.assert_awaited_once()


async def test_a_fresh_check_is_left_alone() -> None:
    """Coming online must not turn every reconnect into a cloud call."""
    coordinator = _coordinator(last_check_age=timedelta(days=2))

    await coordinator._on_state_changed(_snapshot(coordinator.data))

    coordinator.async_request_refresh.assert_not_awaited()


async def test_an_already_online_record_still_triggers() -> None:
    """The bug an offline->online edge had: the record is shared and always online.

    Another coordinator handles the same push first and sets ``online = True``
    on the shared account record, so an edge would see no transition and the
    check would never run in production.
    """
    coordinator = _coordinator(last_check_age=None)
    assert coordinator.device.online is True

    await coordinator._on_state_changed(_snapshot(coordinator.data))

    coordinator.async_request_refresh.assert_awaited_once()


async def test_a_second_push_does_not_retry_immediately() -> None:
    """A check that failed records nothing, so the attempt itself is throttled."""
    coordinator = _coordinator(last_check_age=None)

    await coordinator._on_state_changed(_snapshot(coordinator.data))
    await coordinator._on_state_changed(_snapshot(coordinator.data))

    coordinator.async_request_refresh.assert_awaited_once()


async def test_the_retry_throttle_expires() -> None:
    """A failed check must not be shut out for good, only for the retry window."""
    coordinator = _coordinator(last_check_age=None)

    await coordinator._on_state_changed(_snapshot(coordinator.data))
    coordinator._firmware_check_attempted = (
        dt_util.utcnow() - FIRMWARE_CHECK_RETRY_INTERVAL
    )
    await coordinator._on_state_changed(_snapshot(coordinator.data))

    assert coordinator.async_request_refresh.await_count == 2


async def test_a_ble_only_device_is_not_woken_for_a_check() -> None:
    """The OTA lookup is an HTTP call; with no cloud login there is nothing to ask."""
    coordinator = _coordinator(last_check_age=None, cloud=False)

    await coordinator._on_state_changed(_snapshot(coordinator.data))

    coordinator.async_request_refresh.assert_not_awaited()


async def test_a_reauth_pending_account_is_not_asked() -> None:
    """A quiesced cloud side would only produce a failed call."""
    coordinator = _coordinator(last_check_age=None)
    coordinator.manager.reauth_required = "account@example.com"

    await coordinator._on_state_changed(_snapshot(coordinator.data))

    coordinator.async_request_refresh.assert_not_awaited()


async def test_the_state_push_still_happens_either_way() -> None:
    """The firmware check rides along with the existing push; it does not replace it."""
    coordinator = _coordinator(last_check_age=timedelta(days=2))

    await coordinator._on_state_changed(_snapshot(coordinator.data))

    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
    assert coordinator.device.online is True


async def test_a_completed_cloud_lookup_records_the_check() -> None:
    """Without this the timestamp never moves and every reconnect re-fires."""
    coordinator = _coordinator(last_check_age=None)
    device = coordinator.data
    # Everything the startup probes would otherwise go and fetch.
    device.mower_state.swversion = "1.2.3"
    device.mower_state.model_id = "MODEL"
    device.device_firmwares.main_controller = "1.0.0"
    device.device_firmwares.device_version = "1.2.3"
    coordinator.manager.get_device_by_name.return_value = device
    coordinator.manager.mower.return_value = MagicMock(iot_id="iot-1")
    coordinator.check_firmware_version = AsyncMock()
    coordinator._cloud_api_call = AsyncMock(return_value=MagicMock(data=[]))
    coordinator._store.async_set_firmware_checked = AsyncMock()

    with patch.object(
        MammotionBaseUpdateCoordinator,
        "_async_update_data",
        AsyncMock(return_value=None),
    ):
        await coordinator._async_update_data()

    coordinator._store.async_set_firmware_checked.assert_awaited_once()
    assert (
        coordinator._store.async_set_firmware_checked.await_args.args[0] == _DEVICE_NAME
    )


async def test_a_failed_cloud_lookup_does_not_record_a_check() -> None:
    """A check that never reached the cloud must stay due."""
    coordinator = _coordinator(last_check_age=None)
    device = coordinator.data
    device.mower_state.swversion = "1.2.3"
    device.mower_state.model_id = "MODEL"
    device.device_firmwares.main_controller = "1.0.0"
    device.device_firmwares.device_version = "1.2.3"
    coordinator.manager.get_device_by_name.return_value = device
    coordinator.manager.mower.return_value = MagicMock(iot_id="iot-1")
    coordinator.check_firmware_version = AsyncMock()
    coordinator._cloud_api_call = AsyncMock(return_value=None)
    coordinator._store.async_set_firmware_checked = AsyncMock()

    with patch.object(
        MammotionBaseUpdateCoordinator,
        "_async_update_data",
        AsyncMock(return_value=None),
    ):
        await coordinator._async_update_data()

    coordinator._store.async_set_firmware_checked.assert_not_awaited()
