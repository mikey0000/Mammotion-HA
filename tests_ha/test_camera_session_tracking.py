"""Camera feeds per mower, their names, and which still have a viewer."""

from __future__ import annotations

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
    coordinator._active_camera_sessions = {"webrtc_camera": "session-1"}
    coordinator._webrtc_session_controls = {}
    coordinator.manager.stop_stream = AsyncMock()
    coordinator.device.device_name = "Luba-VS00CLD"

    await MammotionBaseUpdateCoordinator.leave_webrtc_channel(coordinator)

    assert coordinator._active_camera_sessions == {}
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


@pytest.mark.parametrize(
    ("device_name", "states"),
    [("Luba-VS00CLD", [1, 1, 0]), ("Yuka-000CLD", [1, 1, 1])],
)
async def test_token_request_enables_the_rear_slot_only_on_yuka(
    device_name: str, states: list[int]
) -> None:
    """The token asks for both front feeds, and the rear one only on Yuka."""
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"code": 0, "msg": "ok", "data": None})
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.post.return_value = context
    coordinator = MagicMock()
    coordinator.device_name = device_name
    coordinator.device.iot_id = "iot-123"
    coordinator.manager.mammotion_http.ensure_token_valid = AsyncMock()
    coordinator.manager.mammotion_http._headers = {}

    with patch(
        "custom_components.mammotion.coordinator.aiohttp_client.async_get_clientsession",
        return_value=session,
    ):
        await MammotionBaseUpdateCoordinator._request_dual_camera_stream(coordinator)

    assert session.post.call_args.kwargs["json"]["cameraStates"] == [
        {"cameraState": state} for state in states
    ]
