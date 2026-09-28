"""Each Luba 2 camera handler must ignore the other vision peer."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any
from unittest.mock import MagicMock

from custom_components.mammotion.agora_websocket import AgoraWebSocketHandler


def _run(coro: Awaitable[Any]) -> None:
    asyncio.new_event_loop().run_until_complete(coro)


def test_handler_ignores_online_video_and_offline_events_for_other_camera() -> None:
    """Filtering applies to each peer event so camera state stays isolated."""
    handler = AgoraWebSocketHandler(MagicMock(), target_uid=2)
    handler._video_streams[1] = {"ssrcId": 10, "subscribed": True}

    _run(handler._handle_user_online({"_message": {"uid": 1}}))
    _run(handler._handle_add_video_stream({"_message": {"uid": 1, "ssrcId": 11}}))
    _run(handler._handle_user_offline({"_message": {"uid": 1}}))

    assert handler._online_users == set()
    assert handler._video_streams == {1: {"ssrcId": 10, "subscribed": True}}
