"""The renew_token debounce in ``AgoraWebSocketHandler``, against the real handler.

The 2026-05-22 HA log showed the gateway's ``on_token_privilege_will_expire``
event firing every second for 30 s during the pre-expiry window, with the
handler sending a fresh ``renew_token`` request on every event — 30 sends in
30 s before the token expired anyway.  The debounce caps consecutive sends at
one per ``RENEW_TOKEN_DEBOUNCE_SECS`` window, mirroring how the APK only needs
a single renew per expiry cycle.

The equivalent in ``tests/`` had to load ``agora_websocket.py`` by file path
with its imports stubbed; here it is the real module, the real monotonic clock
and the real ``StreamSubscriptionResponse`` the cloud hands back, so the token
that ends up on the wire is the one the cloud actually minted.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.http.model.camera_stream import StreamSubscriptionResponse

from custom_components.mammotion.agora_websocket import AgoraWebSocketHandler

_TOKEN = "007eJxTYFj1Vf7Ewumre3TnJ7fK8vNfake-fake-token-abc"


def _agora_data() -> StreamSubscriptionResponse:
    """Build the cloud's stream subscription, shaped as the API returns it."""
    return StreamSubscriptionResponse(
        appid="app-id",
        openEncrypt=0,
        cameras=[],
        channelName="channel",
        areaCode="AS",
        token=_TOKEN,
        uid=12345,
    )


@pytest.fixture
def handler(hass: HomeAssistant) -> AgoraWebSocketHandler:
    """Build a handler already joined to a channel, with only the socket mocked."""
    handler = AgoraWebSocketHandler(hass=hass)
    handler._websocket = AsyncMock()
    handler._agora_data = _agora_data()
    return handler


def _sent(handler: AgoraWebSocketHandler) -> list[dict[str, Any]]:
    """Decode every frame the handler pushed onto the socket."""
    return [
        json.loads(call.args[0]) for call in handler._websocket.send.await_args_list
    ]


async def test_renew_token_first_call_sends(handler: AgoraWebSocketHandler) -> None:
    """The first renew carries the token the cloud minted for this channel."""
    await handler._send_renew_token()

    assert [(msg["_type"], msg["_message"]["token"]) for msg in _sent(handler)] == [
        ("renew_token", _TOKEN)
    ]


async def test_renew_token_storm_is_debounced(handler: AgoraWebSocketHandler) -> None:
    """The gateway's once-per-second pre-expiry storm produces one send."""
    for _ in range(30):
        await handler._send_renew_token()

    assert len(_sent(handler)) == 1, (
        "The renew_token debounce regressed and the 30-send storm is back"
    )


async def test_renew_token_passes_after_did_expire(
    handler: AgoraWebSocketHandler,
) -> None:
    """``on_token_privilege_did_expire`` clears the debounce; the next renew goes."""
    await handler._send_renew_token()
    await handler._send_renew_token()

    handler._last_renew_token_at = 0.0
    await handler._send_renew_token()

    assert len(_sent(handler)) == 2


async def test_renew_token_failed_send_clears_debounce(
    handler: AgoraWebSocketHandler,
) -> None:
    """A send that never went out must not hold the debounce for 30 s."""
    handler._websocket.send.side_effect = ConnectionError("ws closed")

    await handler._send_renew_token()
    assert handler._last_renew_token_at == 0.0

    handler._websocket.send.side_effect = None
    await handler._send_renew_token()

    assert handler._websocket.send.await_count == 2


async def test_renew_token_no_op_when_no_websocket(
    handler: AgoraWebSocketHandler,
) -> None:
    """Neither a closed socket nor a missing subscription may raise."""
    socket = handler._websocket
    handler._websocket = None
    await handler._send_renew_token()

    handler._websocket = socket
    handler._agora_data = None
    await handler._send_renew_token()

    assert socket.send.await_count == 0


async def test_concurrent_renew_calls_only_send_once(
    handler: AgoraWebSocketHandler,
) -> None:
    """Concurrent events still collapse to one send — the debounce gates first."""
    await asyncio.gather(*(handler._send_renew_token() for _ in range(10)))

    assert handler._websocket.send.await_count == 1
