"""Camera feeds per mower, their names, and which still have a viewer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.mammotion.camera import CAMERAS, MammotionWebRTCCamera
from custom_components.mammotion.coordinator import (
    MammotionBaseUpdateCoordinator,
    vision_camera_slots,
)


@pytest.mark.parametrize(
    ("device_name", "slots"),
    [
        ("Luba-AAAAAA", 0),
        ("Luba-VS00CLD", 2),
        ("Luba-VP00CLD", 2),
        ("Yuka-000CLD", 3),
    ],
)
def test_vision_camera_slots(device_name: str, slots: int) -> None:
    """Every vision mower gets two feeds; Yuka adds the rear one."""
    assert vision_camera_slots(device_name) == slots


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
