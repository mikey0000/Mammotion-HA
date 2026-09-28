"""A second camera joining as the same Agora uid quits the first (code 2003)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant

from custom_components.mammotion.agora_websocket import AgoraWebSocketHandler

_QUIT = {
    "_type": "on_notification",
    "_message": {"action": "quit", "code": 2003, "detail": "ERR_REPEAT_JOIN"},
}


async def test_a_quit_ends_the_session(hass: HomeAssistant) -> None:
    """Ignoring it left the camera frozen on its last frame."""
    ended = AsyncMock()
    handler = AgoraWebSocketHandler(hass, target_uid=1, session_ended=ended)

    await handler._handle_notification(_QUIT)
    await hass.async_block_till_done()

    ended.assert_awaited_once()


async def test_other_notifications_leave_the_session_alone(
    hass: HomeAssistant,
) -> None:
    """Only a quit ends it."""
    ended = AsyncMock()
    handler = AgoraWebSocketHandler(hass, target_uid=1, session_ended=ended)

    await handler._handle_notification(
        {"_type": "on_notification", "_message": {"action": "warn", "code": 1}}
    )
    await hass.async_block_till_done()

    ended.assert_not_awaited()


async def test_a_quit_without_a_callback_is_only_logged() -> None:
    """The callback is optional."""
    handler = AgoraWebSocketHandler(MagicMock(), target_uid=1)

    await handler._handle_notification(_QUIT)
