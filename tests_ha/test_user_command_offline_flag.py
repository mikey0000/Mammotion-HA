"""A user command passes the cloud's advisory offline flag; background traffic does not.

``mqtt_reported_offline`` is only as fresh as the last thing the cloud pushed, and a
cloud send is a synchronous request the cloud rejects with ``DeviceOfflineException``
when the mower really is away.  So a direct priority (``USER`` / ``EMERGENCY``) is
sent and left for the cloud to refuse; only a terminal transport failure refuses it
up front.  The handle here is the real ``DeviceHandle``, so the gate is pymammotion's.
"""

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceHandle
from pymammotion.messaging.command_queue import Priority
from user_command_support import make_cloud_handle, make_coordinator

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_DEVICE_NAME = "Luba-VAME9R5S"


def _handle(*, reported_offline: bool, cloud_usable: bool = True) -> DeviceHandle:
    """Return a real handle whose only transport is cloud MQTT."""
    return make_cloud_handle(
        _DEVICE_NAME,
        MowingDevice(),
        reported_offline=reported_offline,
        cloud_usable=cloud_usable,
    )


def _coordinator(handle: DeviceHandle) -> MammotionReportUpdateCoordinator:
    return make_coordinator(
        MammotionReportUpdateCoordinator,
        MowingDevice(),
        device_name=_DEVICE_NAME,
        handle=handle,
    )


def _rejected_as_offline() -> DeviceOfflineException:
    """Return what the cloud send raises for a device it reports offline."""
    return DeviceOfflineException(29003, "iot-1")


@pytest.mark.regression
@pytest.mark.parametrize("priority", [Priority.USER, Priority.EMERGENCY])
async def test_a_user_command_is_sent_when_the_cloud_last_reported_the_mower_offline(
    priority: Priority,
) -> None:
    """The pre-check used ``has_usable_transport``, which honours the offline flag.

    So a nudge button or camera ``move_*`` press on a mower the cloud had last
    called offline returned False without sending anything: the press was
    silently dropped, although the library lets a direct priority through and
    lets the cloud reject it if the mower really is away.
    """
    coordinator = _coordinator(_handle(reported_offline=True))

    sent = await coordinator.async_send_command(
        "move_forward", priority=priority, linear=0.4
    )

    assert sent is True
    coordinator.manager.send_command_with_args.assert_awaited_once()


@pytest.mark.regression
async def test_a_user_send_and_wait_is_sent_when_the_cloud_reported_the_mower_offline() -> (
    None
):
    """The same pre-check dropped a waited-on user command (voice volume, start job)."""
    coordinator = _coordinator(_handle(reported_offline=True))

    await coordinator.async_send_and_wait(
        "set_car_volume", "set_audio", priority=Priority.USER, volume=50
    )

    coordinator.manager.send_command_and_wait.assert_awaited_once()


@pytest.mark.parametrize("priority", [Priority.NORMAL, Priority.BACKGROUND])
async def test_a_background_command_is_not_sent_when_the_cloud_reported_the_mower_offline(
    priority: Priority,
) -> None:
    """Nobody waits on background traffic, so it keeps the strict gate."""
    coordinator = _coordinator(_handle(reported_offline=True))

    sent = await coordinator.async_send_command("get_report_cfg", priority=priority)

    assert sent is False
    coordinator.manager.send_command_with_args.assert_not_awaited()


async def test_a_user_command_is_not_sent_over_a_terminally_failed_transport() -> None:
    """Only the offline flag is waived: an unusable transport still refuses everyone."""
    coordinator = _coordinator(_handle(reported_offline=True, cloud_usable=False))

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_send_command("move_forward", priority=Priority.USER)

    assert raised.value.translation_key == "command_failed"
    coordinator.manager.send_command_with_args.assert_not_awaited()


async def test_a_user_command_the_cloud_rejects_as_offline_is_reported() -> None:
    """The rejection marks the mower offline and surfaces: a silent no-op is unactionable."""
    coordinator = _coordinator(_handle(reported_offline=True))
    coordinator.manager.send_command_with_args.side_effect = _rejected_as_offline()

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_send_command("move_forward", priority=Priority.USER)

    assert raised.value.translation_key == "command_failed"
    assert coordinator.manager.get_device_by_name.return_value.online is False


async def test_a_background_command_the_cloud_rejects_as_offline_stays_quiet() -> None:
    """It still marks the mower offline, but nobody is waiting, so it does not raise."""
    coordinator = _coordinator(_handle(reported_offline=False))
    coordinator.manager.send_command_with_args.side_effect = _rejected_as_offline()

    sent = await coordinator.async_send_command("get_report_cfg")

    assert sent is False
    assert coordinator.manager.get_device_by_name.return_value.online is False


@pytest.mark.regression
@pytest.mark.parametrize("priority", [Priority.USER, Priority.EMERGENCY])
async def test_a_user_send_and_wait_the_cloud_rejects_as_offline_is_reported(
    priority: Priority,
) -> None:
    """``async_send_and_wait`` marked the mower offline and returned silently.

    So a waited-on press (voice volume, blades, start job) the cloud refused did
    nothing and said nothing, unlike the same rejection through
    ``async_send_command``.
    """
    coordinator = _coordinator(_handle(reported_offline=True))
    coordinator.manager.send_command_and_wait.side_effect = _rejected_as_offline()

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_send_and_wait(
            "set_car_volume", "set_audio", priority=priority, volume=50
        )

    assert raised.value.translation_key == "command_failed"
    assert coordinator.manager.get_device_by_name.return_value.online is False


async def test_a_background_send_and_wait_the_cloud_rejects_as_offline_stays_quiet() -> (
    None
):
    """It still marks the mower offline, but nobody is waiting, so it does not raise."""
    coordinator = _coordinator(_handle(reported_offline=False))
    coordinator.manager.send_command_and_wait.side_effect = _rejected_as_offline()

    await coordinator.async_send_and_wait("get_car_audio_cfg", "audio_cfg")

    coordinator.manager.send_command_and_wait.assert_awaited_once()
    assert coordinator.manager.get_device_by_name.return_value.online is False
