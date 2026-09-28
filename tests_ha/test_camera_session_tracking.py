"""Camera feeds per mower, their names, and which still have a viewer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.mammotion.camera import CAMERAS, MammotionWebRTCCamera
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator


@pytest.mark.parametrize(
    ("device_name", "has_rear"),
    [("Luba-VS00CLD", False), ("Luba-VP00CLD", False), ("Yuka-000CLD", True)],
)
def test_only_yuka_has_the_rear_camera(device_name: str, has_rear: bool) -> None:
    """Left and right exist on every vision mower; rear is gated on Yuka."""
    assert [description.exists_fn(device_name) for description in CAMERAS] == [
        True,
        True,
        has_rear,
    ]


async def test_stop_video_forgets_every_viewer() -> None:
    """A stale viewer would make the next offer reuse the stopped stream's token."""
    coordinator = MagicMock()
    coordinator._cameras_in_use = {"webrtc_camera"}
    coordinator._webrtc_session_controls = {}
    coordinator.manager.stop_stream = AsyncMock()
    coordinator.device.device_name = "Luba-VS00CLD"

    await MammotionBaseUpdateCoordinator.leave_webrtc_channel(coordinator)

    assert coordinator._cameras_in_use == set()
    coordinator.manager.stop_stream.assert_awaited_once_with("Luba-VS00CLD")


def test_camera_names_come_from_the_translation_key() -> None:
    """A None name would label every feed with the bare device name."""
    coordinator = MagicMock()
    coordinator.device.device_name = "Luba-VS00CLD"
    coordinator.unique_name = "Luba-VS00CLD"
    translations = {
        f"component.mammotion.entity.camera.{description.key}.name": description.key
        for description in CAMERAS
    }

    names = []
    for description in CAMERAS:
        camera = MammotionWebRTCCamera(coordinator, description, MagicMock())
        camera.platform_data = SimpleNamespace(
            platform_name="mammotion", domain="camera"
        )
        names.append(camera._name_internal(None, translations))

    assert names == ["webrtc_camera", "webrtc_camera_right", "webrtc_camera_rear"]


def _token_coordinator(*responses: MagicMock) -> MagicMock:
    coordinator = MagicMock()
    coordinator.device_name = "Luba-VS00CLD"
    coordinator.device.iot_id = "iot-123"
    coordinator.streams_all_cameras = True
    coordinator._stream_data = None
    coordinator.manager.get_stream_subscription = AsyncMock(side_effect=responses)
    return coordinator


def _no_agora() -> object:
    agora = MagicMock()
    agora.return_value.__aenter__ = AsyncMock(side_effect=OSError)
    agora.return_value.__aexit__ = AsyncMock(return_value=None)
    return patch("custom_components.mammotion.coordinator.AgoraAPIClient", agora)


async def test_one_token_request_asks_for_every_camera() -> None:
    """The library builds the camera slots; a granted token enables every feed."""
    coordinator = _token_coordinator(MagicMock(code=0, data=MagicMock()))

    with _no_agora():
        await MammotionBaseUpdateCoordinator.async_check_stream_expiry(
            coordinator, force=True
        )

    coordinator.manager.get_stream_subscription.assert_awaited_once_with(
        "Luba-VS00CLD", "iot-123", all_cameras=True
    )
    assert coordinator._all_cameras_streaming is True


async def test_a_rejected_all_camera_token_falls_back_to_the_left_camera() -> None:
    """The left feed keeps working; the others report the stream unavailable."""
    single = MagicMock(code=0, data=MagicMock())
    coordinator = _token_coordinator(MagicMock(code=500, data=None), single)

    with _no_agora():
        await MammotionBaseUpdateCoordinator.async_check_stream_expiry(
            coordinator, force=True
        )

    assert coordinator.manager.get_stream_subscription.await_args_list[-1].args == (
        "Luba-VS00CLD",
        "iot-123",
    )
    coordinator.set_stream_data.assert_called_once_with(single)
    assert coordinator._all_cameras_streaming is False


async def test_a_failed_token_request_keeps_the_last_camera_state() -> None:
    """A sibling camera mid-offer must not see its feed switched off by an error."""
    coordinator = _token_coordinator()
    coordinator.manager.get_stream_subscription = AsyncMock(side_effect=TimeoutError)
    coordinator._all_cameras_streaming = True

    await MammotionBaseUpdateCoordinator.async_check_stream_expiry(
        coordinator, force=True
    )

    assert coordinator._all_cameras_streaming is True


async def test_the_last_camera_to_lose_its_viewer_stops_the_mower() -> None:
    """A sibling camera still being watched keeps the mower streaming."""
    coordinator = MagicMock()
    coordinator._cameras_in_use = {"webrtc_camera", "webrtc_camera_right"}
    coordinator._camera_session_lock = asyncio.Lock()
    coordinator.leave_webrtc_channel = AsyncMock()

    await MammotionBaseUpdateCoordinator.async_release_camera_session(
        coordinator, "webrtc_camera"
    )
    coordinator.leave_webrtc_channel.assert_not_awaited()

    await MammotionBaseUpdateCoordinator.async_release_camera_session(
        coordinator, "webrtc_camera_right"
    )
    await MammotionBaseUpdateCoordinator.async_release_camera_session(
        coordinator, "webrtc_camera_right"
    )
    coordinator.leave_webrtc_channel.assert_awaited_once()
