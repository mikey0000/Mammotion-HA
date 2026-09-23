"""WebRTC session teardown on the Mammotion camera entity.

The entity must hang its teardown on ``close_webrtc_session``: that is the only
hook Home Assistant invokes for a *native* WebRTC camera (``camera/webrtc.py``
registers ``functools.partial(camera.close_webrtc_session, session_id)`` as the
websocket subscription's teardown, and the base implementation no-ops because
native cameras have no ``_webrtc_provider``).  A coroutine named
``async_close_webrtc_session`` is never called by core; the synchronous hook
schedules it so the coordinator can stop the mower after the final feed closes.

Here the hook is invoked exactly as core invokes it — synchronously, return
value discarded, on a real event loop — and the ICE-server registration is read
back out of the real ``web_rtc`` integration instead of a mock.
"""

from __future__ import annotations

import functools
import inspect
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.camera import Camera
from homeassistant.components.web_rtc import async_get_ice_servers
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from webrtc_models import RTCIceServer

from custom_components.mammotion.camera import CAMERAS, MammotionWebRTCCamera
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_DEVICE = "Luba-VS00CLD"

# Shaped like the entries async_setup_entry builds from Agora's TURN addresses.
_ICE_SERVER = RTCIceServer(
    urls="turn:45.196.22.13:3478?transport=udp",
    username="agora-user",
    credential="agora-credential",
)


@pytest.fixture
async def camera(hass: HomeAssistant) -> MammotionWebRTCCamera:
    """Build a real camera entity with its Agora handler and coordinator mocked out."""
    assert await async_setup_component(hass, "web_rtc", {})
    coordinator = MagicMock()
    coordinator.unique_name = _DEVICE
    coordinator.device.device_name = _DEVICE
    coordinator.manager.stop_stream = AsyncMock()
    coordinator.async_release_camera_session = AsyncMock()
    coordinator.ice_servers = [_ICE_SERVER]
    entity = MammotionWebRTCCamera(coordinator, CAMERAS[0], hass)
    entity.hass = hass
    entity.entity_id = "camera.luba_vs00cld"
    entity._agora_handler = MagicMock(disconnect=AsyncMock())
    return entity


def _core_teardown(camera: MammotionWebRTCCamera, session_id: str) -> None:
    """Invoke the hook the way ``camera/webrtc.py`` does on unsubscribe."""
    functools.partial(camera.close_webrtc_session, session_id)()


def test_the_hook_core_calls_is_the_one_overridden() -> None:
    """Core only ever calls ``close_webrtc_session``, and calls it synchronously."""
    assert MammotionWebRTCCamera.close_webrtc_session is not Camera.close_webrtc_session
    # A coroutine function here would be built and dropped, never awaited.
    assert not inspect.iscoroutinefunction(MammotionWebRTCCamera.close_webrtc_session)


async def test_closing_the_last_session_tears_the_stream_down(
    hass: HomeAssistant, camera: MammotionWebRTCCamera
) -> None:
    """The frontend dropping its only session tears the stream down."""
    camera._sessions.add("session-1")
    camera._active_session_id = "session-1"

    _core_teardown(camera, "session-1")
    await hass.async_block_till_done()

    camera._agora_handler.disconnect.assert_awaited_once()
    camera.coordinator.async_release_camera_session.assert_awaited_once_with(
        "webrtc_camera", "session-1"
    )
    camera.coordinator.manager.stop_stream.assert_not_awaited()


async def test_teardown_waits_for_the_last_viewer(
    hass: HomeAssistant, camera: MammotionWebRTCCamera
) -> None:
    """A second viewer still watching keeps the stream up."""
    camera._sessions.update({"session-1", "session-2"})
    camera._active_session_id = "session-2"

    _core_teardown(camera, "session-1")
    await hass.async_block_till_done()
    camera._agora_handler.disconnect.assert_not_awaited()

    _core_teardown(camera, "session-2")
    await hass.async_block_till_done()
    camera._agora_handler.disconnect.assert_awaited_once()
    camera.coordinator.async_release_camera_session.assert_awaited_once_with(
        "webrtc_camera", "session-2"
    )


async def test_teardown_leaves_the_channel_before_stopping_the_encoder(
    camera: MammotionWebRTCCamera,
) -> None:
    """Order matters: the app leaves the Agora channel, then sends vi_switch=0."""
    order: list[str] = []
    camera._agora_handler.disconnect = AsyncMock(
        side_effect=lambda: order.append("leave")
    )
    camera.coordinator.manager.stop_stream = AsyncMock(
        side_effect=lambda _name: order.append("stop")
    )

    await camera.async_teardown_stream()

    assert order == ["leave", "stop"]


async def test_stop_command_failure_still_leaves_the_channel(
    camera: MammotionWebRTCCamera,
) -> None:
    """A mower that is offline must not strand the Agora socket open."""
    camera.coordinator.manager.stop_stream = AsyncMock(side_effect=OSError("offline"))

    await camera.async_teardown_stream()

    camera._agora_handler.disconnect.assert_awaited_once()


async def test_unknown_session_does_not_tear_down_a_live_stream(
    hass: HomeAssistant, camera: MammotionWebRTCCamera
) -> None:
    """A stale close for an already-gone session must not kill a newer one."""
    camera._sessions.add("session-2")

    _core_teardown(camera, "session-1")
    await hass.async_block_till_done()

    camera._agora_handler.disconnect.assert_not_awaited()
    assert camera._sessions == {"session-2"}


async def test_entity_registers_itself_for_service_driven_teardown(
    camera: MammotionWebRTCCamera,
) -> None:
    """``mammotion.stop_video`` reaches the same teardown the frontend uses.

    ``leave_webrtc_channel`` was an un-overridden stub on the coordinator, so
    the service silently did nothing; the entity has to attach itself.
    """
    await camera.async_added_to_hass()

    camera.coordinator.register_webrtc_session_control.assert_called_once_with(
        camera, "webrtc_camera"
    )


async def test_removal_tears_down_and_detaches(
    camera: MammotionWebRTCCamera,
) -> None:
    """Unload/reload must not leave a stream running with no entity behind it."""
    camera._sessions.add("session-1")

    await camera.async_will_remove_from_hass()

    camera.coordinator.register_webrtc_session_control.assert_called_once_with(
        None, "webrtc_camera"
    )
    camera._agora_handler.disconnect.assert_awaited_once()
    camera.coordinator.manager.stop_stream.assert_awaited_once_with(_DEVICE)
    assert camera._sessions == set()


async def test_reload_does_not_leave_ice_servers_registered(
    hass: HomeAssistant, camera: MammotionWebRTCCamera
) -> None:
    """Core keeps the getter in a global list until the returned callback runs.

    Dropping that callback left another copy behind on every reload, and the
    browser gathered a duplicate set of Agora relay candidates for each one.
    """
    await camera.async_added_to_hass()
    assert async_get_ice_servers(hass).count(_ICE_SERVER) == 1

    await camera.async_will_remove_from_hass()
    assert _ICE_SERVER not in async_get_ice_servers(hass)

    await camera.async_added_to_hass()
    await camera.async_will_remove_from_hass()
    await camera.async_added_to_hass()
    assert async_get_ice_servers(hass).count(_ICE_SERVER) == 1


async def test_a_camera_built_before_the_first_refresh_still_serves_ice(
    hass: HomeAssistant,
) -> None:
    """The coordinator seeded this as None, which core cannot extend.

    ``async_setup_entry`` happens to assign the list before building entities,
    so the ``None`` never escaped; any path that reversed that order made
    ``get_ice_servers`` return ``None`` and core's ``servers.extend(...)`` raise
    ``TypeError`` — taking out ICE for every camera in the install, not just
    this one.
    """
    assert await async_setup_component(hass, "web_rtc", {})
    entry = MockConfigEntry(domain="mammotion", unique_id="account@example.com")
    entry.add_to_hass(hass)
    coordinator = MammotionReportUpdateCoordinator(
        hass, entry, MagicMock(), MagicMock(), timedelta(seconds=30)
    )
    assert coordinator.ice_servers == []

    coordinator.device.device_name = _DEVICE
    entity = MammotionWebRTCCamera(coordinator, CAMERAS[0], hass)
    entity.hass = hass
    entity.entity_id = "camera.luba_vs00cld"
    assert entity.get_ice_servers() == []

    # Core's own defaults are already registered; ours must add nothing and,
    # crucially, must not make the whole list unbuildable.
    before = async_get_ice_servers(hass)
    await entity.async_added_to_hass()
    assert async_get_ice_servers(hass) == before


async def test_a_token_refresh_reaches_the_next_session(
    hass: HomeAssistant, camera: MammotionWebRTCCamera
) -> None:
    """Agora rotates TURN credentials on every stream-token refresh.

    The entity used to snapshot the list at construction, so once the
    coordinator rebound it the browser kept gathering relay candidates with
    the setup-time username and got 401s.  Core asks again for each new
    session, so reading through is enough.
    """
    await camera.async_added_to_hass()
    assert _ICE_SERVER in async_get_ice_servers(hass)

    rotated = RTCIceServer(
        urls="turn:45.196.22.13:3478?transport=udp",
        username="agora-user-2",
        credential="agora-credential-2",
    )
    camera.coordinator.ice_servers = [rotated]

    servers = async_get_ice_servers(hass)
    assert rotated in servers
    assert _ICE_SERVER not in servers
