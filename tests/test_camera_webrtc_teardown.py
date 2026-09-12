"""Tests for WebRTC session teardown on the Mammotion camera entity.

The entity must hang its teardown on ``close_webrtc_session``: that is the only
hook Home Assistant invokes for a *native* WebRTC camera (``camera/webrtc.py``
registers it as the websocket subscription's teardown, and the base
implementation no-ops because native cameras have no ``_webrtc_provider``).  A
coroutine named ``async_close_webrtc_session`` is never called by core, so the
Agora socket, its ping loop and the mower's encoder all outlive the viewer.
"""

from __future__ import annotations

import asyncio
import inspect
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_camera_ble_only import _load_camera


@pytest.fixture(scope="module")
def camera_module() -> types.ModuleType:
    """Load the real camera module once per test module."""
    return _load_camera()


@pytest.fixture
def camera(camera_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a camera entity with its Agora handler and coordinator mocked out."""
    monkeypatch.setattr(camera_module, "async_register_ice_servers", MagicMock())
    coordinator = MagicMock()
    coordinator.device.device_name = "Luba-Test"
    coordinator.manager.stop_stream = AsyncMock()
    entity = camera_module.MammotionWebRTCCamera.__new__(
        camera_module.MammotionWebRTCCamera
    )
    entity.coordinator = coordinator
    entity._agora_handler = MagicMock(disconnect=AsyncMock())
    entity._sessions = set()
    entity._teardown_lock = asyncio.Lock()
    entity.hass = MagicMock()
    return entity


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def test_core_calls_a_hook_that_exists(camera_module: types.ModuleType) -> None:
    """The hook must carry the name core invokes, and be a plain callback."""
    cls = camera_module.MammotionWebRTCCamera
    assert hasattr(cls, "close_webrtc_session")
    # Core calls it synchronously via functools.partial; a coroutine function
    # here would be built and dropped, never awaited.
    assert not inspect.iscoroutinefunction(cls.close_webrtc_session)


def test_closing_the_last_session_schedules_teardown(camera: Any) -> None:
    """The frontend dropping its only session tears the stream down."""
    camera._sessions.add("session-1")

    camera.close_webrtc_session("session-1")

    camera.hass.async_create_task.assert_called_once()
    _run(camera.hass.async_create_task.call_args.args[0])
    camera._agora_handler.disconnect.assert_awaited_once()
    camera.coordinator.manager.stop_stream.assert_awaited_once_with("Luba-Test")


def test_teardown_waits_for_the_last_viewer(camera: Any) -> None:
    """A second viewer still watching keeps the stream up."""
    camera._sessions.update({"session-1", "session-2"})

    camera.close_webrtc_session("session-1")
    camera.hass.async_create_task.assert_not_called()

    camera.close_webrtc_session("session-2")
    camera.hass.async_create_task.assert_called_once()
    camera.hass.async_create_task.call_args.args[0].close()


def test_teardown_leaves_the_channel_before_stopping_the_encoder(camera: Any) -> None:
    """Order matters: the app leaves the Agora channel, then sends vi_switch=0."""
    order: list[str] = []
    camera._agora_handler.disconnect = AsyncMock(
        side_effect=lambda: order.append("leave")
    )
    camera.coordinator.manager.stop_stream = AsyncMock(
        side_effect=lambda _name: order.append("stop")
    )

    _run(camera.async_teardown_stream())

    assert order == ["leave", "stop"]


def test_stop_command_failure_still_leaves_the_channel(camera: Any) -> None:
    """A mower that is offline must not strand the Agora socket open."""
    camera.coordinator.manager.stop_stream = AsyncMock(side_effect=OSError("offline"))

    _run(camera.async_teardown_stream())

    camera._agora_handler.disconnect.assert_awaited_once()


def test_unknown_session_does_not_tear_down_a_live_stream(camera: Any) -> None:
    """A stale close for an already-gone session must not kill a newer one."""
    camera._sessions.add("session-2")

    camera.close_webrtc_session("session-1")

    camera.hass.async_create_task.assert_not_called()
    assert camera._sessions == {"session-2"}


def test_entity_registers_itself_for_service_driven_teardown(camera: Any) -> None:
    """``mammotion.stop_video`` reaches the same teardown the frontend uses.

    ``leave_webrtc_channel`` was an un-overridden stub on the coordinator, so
    the service silently did nothing; the entity has to attach itself.
    """
    camera.async_write_ha_state = MagicMock()
    _run(camera.async_added_to_hass())

    camera.coordinator.register_webrtc_session_control.assert_called_once_with(camera)


def test_removal_tears_down_and_detaches(camera: Any) -> None:
    """Unload/reload must not leave a stream running with no entity behind it."""
    camera._sessions.add("session-1")

    _run(camera.async_will_remove_from_hass())

    camera.coordinator.register_webrtc_session_control.assert_called_once_with(None)
    camera._agora_handler.disconnect.assert_awaited_once()
    camera.coordinator.manager.stop_stream.assert_awaited_once_with("Luba-Test")
    assert camera._sessions == set()
