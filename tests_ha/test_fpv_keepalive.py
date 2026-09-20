"""The 4G FPV keep-alive loop in ``AgoraWebSocketHandler``.

Over cellular the mower's video encoder stops publishing unless it is re-armed
with ``refresh_fpv`` roughly every 3 s (the app's
``FPV4GVideoStateMannager.refreshInterval = 3000ms``).  ``_fpv_keepalive_loop``
pokes the device on that cadence via the injected ``keepalive`` callback, stops
quietly when the callback reports it isn't needed (WiFi), and ends the stream
when the cloud's free-minutes budget (``availableTime``) is exhausted.

Against the real Home Assistant "ends the stream" is observed on the socket —
the channel is left and the connection closed — rather than by asserting that
a task was created.  Only the handler module's own ``asyncio`` and ``time``
references are swapped, so HA's clock and scheduler are untouched.
"""

from __future__ import annotations

import asyncio
import json
import types
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.mammotion import agora_websocket
from custom_components.mammotion.agora_websocket import (
    FPV_KEEPALIVE_INTERVAL_SECS,
    AgoraWebSocketHandler,
)


class _SleepSpy:
    """Stands in for the handler module's ``asyncio`` so only its sleeps are faked."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


@pytest.fixture
def handler(hass: HomeAssistant) -> AgoraWebSocketHandler:
    """Build a handler mid-session on 4G, with only the socket mocked."""
    handler = AgoraWebSocketHandler(hass=hass, keepalive=AsyncMock(return_value=True))
    handler._websocket = AsyncMock()
    handler._joined = True
    handler._connection_state = "CONNECTED"
    return handler


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> _SleepSpy:
    """Record the keep-alive cadence instead of waiting it out."""
    spy = _SleepSpy()
    monkeypatch.setattr(agora_websocket, "asyncio", spy)
    return spy


def _left_channel(socket: AsyncMock) -> bool:
    """Whether the handler sent Agora a ``leave`` on this socket."""
    return any(
        json.loads(call.args[0])["_type"] == "leave"
        for call in socket.send.await_args_list
    )


async def test_keepalive_wifi_stops_without_ending_stream(
    handler: AgoraWebSocketHandler, sleeps: _SleepSpy
) -> None:
    """A callback returning False (WiFi) exits after one poke, stream running."""
    handler._keepalive = AsyncMock(return_value=False)

    await handler._fpv_keepalive_loop(None)

    assert handler._keepalive.await_count == 1
    assert sleeps.delays == []
    assert not _left_channel(handler._websocket)
    assert handler._connection_state == "CONNECTED"


async def test_keepalive_4g_pokes_on_the_apps_cadence(
    handler: AgoraWebSocketHandler, sleeps: _SleepSpy
) -> None:
    """On 4G the loop pokes every interval until the callback stops it."""
    handler._keepalive = AsyncMock(side_effect=[True, True, False])

    await handler._fpv_keepalive_loop(None)  # None => no budget deadline

    assert handler._keepalive.await_count == 3
    # One wait after each of the two successful pokes, none after the final
    # False that ended the loop.
    assert sleeps.delays == [FPV_KEEPALIVE_INTERVAL_SECS] * 2
    assert not _left_channel(handler._websocket)


async def test_keepalive_budget_exhausted_ends_stream(
    hass: HomeAssistant,
    handler: AgoraWebSocketHandler,
    sleeps: _SleepSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exhausted 4G free-minutes budget ends the stream, not just the loop."""
    # The first reading computes the deadline (0 + 5); the post-poke check reads
    # 100, which is past it.
    times = iter([0.0, 100.0])
    monkeypatch.setattr(
        agora_websocket,
        "time",
        types.SimpleNamespace(monotonic=lambda: next(times, 100.0)),
    )
    socket = handler._websocket

    await handler._fpv_keepalive_loop(5)
    await hass.async_block_till_done()

    handler._keepalive.assert_awaited_once()
    assert _left_channel(socket)
    socket.close.assert_awaited_once()
    assert handler._websocket is None
    assert handler._connection_state == "DISCONNECTED"


async def test_disconnect_cancels_keepalive_task(
    handler: AgoraWebSocketHandler,
) -> None:
    """disconnect() cancels a running keep-alive task and clears the handle."""

    async def _runner() -> None:
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(_runner())
    handler._fpv_keepalive_task = task

    await handler.disconnect()
    await asyncio.sleep(0)  # let the cancellation propagate

    assert task.cancelled()
    assert handler._fpv_keepalive_task is None
