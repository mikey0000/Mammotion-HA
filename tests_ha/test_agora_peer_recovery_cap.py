"""The bounded peer-recovery loop in ``AgoraWebSocketHandler``.

When the mower drops out of the Agora channel the handler re-requests the
stream so the session survives a transient blip.  Without a cap that retry is
unconditional: a mower that keeps rejoining and quitting (the ~60 s ``Quit`` /
``ServerTimeOut`` cycle seen in the wild) gets resurrected forever, so an
unwatched stream runs until Home Assistant restarts.  These tests pin the
attempt budget, the window that forgives an occasional drop, and the reset on
session boundaries.

Against the real Home Assistant the recovery is driven the way the gateway
drives it — ``_schedule_peer_recovery`` on a real event loop — rather than by
awaiting the debounce body directly.  Only the module's ``time`` reference is
swapped, so Home Assistant's own clock and ``asyncio.sleep`` are untouched.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.mammotion.agora_websocket import AgoraWebSocketHandler

# Well past PEER_RECOVER_COOLDOWN_SECS so the first recovery isn't itself
# suppressed by the cooldown (which compares against an initial 0.0).
CLOCK_BASE = 10_000.0

_PEER_UID = 1


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Settable monotonic clock for the handler; element 0 is the reading."""
    now = [CLOCK_BASE]
    monkeypatch.setattr(
        "custom_components.mammotion.agora_websocket.time",
        types.SimpleNamespace(monotonic=lambda: now[0]),
    )
    return now


@pytest.fixture
def handler(hass: HomeAssistant) -> AgoraWebSocketHandler:
    """Build a handler wired for recovery, 'connected' with the peer gone."""
    handler = AgoraWebSocketHandler(hass=hass, recover_stream=AsyncMock())
    handler._websocket = AsyncMock()
    handler._connection_state = "CONNECTED"
    # The real 2 s rejoin grace would make every case below wait for wall time.
    handler.PEER_REJOIN_DEBOUNCE_SECS = 0
    return handler


async def _peer_left(
    handler: AgoraWebSocketHandler, clock: list[float], at: float
) -> None:
    """Report the peer gone at ``CLOCK_BASE + at`` and let the recovery settle."""
    clock[0] = CLOCK_BASE + at
    handler._schedule_peer_recovery(_PEER_UID)
    await handler.hass.async_block_till_done()


async def test_recovery_stops_at_the_attempt_cap(
    handler: AgoraWebSocketHandler, clock: list[float]
) -> None:
    """A mower that never settles is retried a bounded number of times."""
    cap = handler.PEER_RECOVER_MAX_ATTEMPTS
    # Space each drop past the cooldown but well inside the reset window, which
    # is the thrash pattern the cap exists to stop.
    step = handler.PEER_RECOVER_COOLDOWN_SECS + 1.0
    assert step * (cap + 3) < handler.PEER_RECOVER_RESET_SECS

    for i in range(cap + 3):
        await _peer_left(handler, clock, at=i * step)

    assert handler._recover_stream.await_count == cap


async def test_settled_stream_forgives_an_occasional_drop(
    handler: AgoraWebSocketHandler, clock: list[float]
) -> None:
    """A drop after a long healthy run starts a fresh budget."""
    cap = handler.PEER_RECOVER_MAX_ATTEMPTS
    step = handler.PEER_RECOVER_COOLDOWN_SECS + 1.0
    for i in range(cap):
        await _peer_left(handler, clock, at=i * step)
    assert handler._recover_stream.await_count == cap

    await _peer_left(handler, clock, at=cap * step)
    assert handler._recover_stream.await_count == cap

    await _peer_left(
        handler, clock, at=cap * step + handler.PEER_RECOVER_RESET_SECS + 1
    )
    assert handler._recover_stream.await_count == cap + 1


async def test_cooldown_still_suppresses_rapid_retries(
    handler: AgoraWebSocketHandler, clock: list[float]
) -> None:
    """Back-to-back drops inside the cooldown don't burn the budget."""
    await _peer_left(handler, clock, at=0.0)
    await _peer_left(handler, clock, at=1.0)

    assert handler._recover_stream.await_count == 1
    assert handler._peer_recover_attempts == 1


async def test_no_recovery_once_the_viewer_is_gone(
    handler: AgoraWebSocketHandler, clock: list[float]
) -> None:
    """A closed websocket means nobody is watching — never re-request."""
    handler._websocket = None

    await _peer_left(handler, clock, at=0.0)

    handler._recover_stream.assert_not_awaited()


async def test_peer_rejoining_cancels_the_recovery(
    handler: AgoraWebSocketHandler, clock: list[float]
) -> None:
    """A peer back online within the debounce window needs no recovery."""
    handler._online_users.add(_PEER_UID)

    await _peer_left(handler, clock, at=0.0)

    handler._recover_stream.assert_not_awaited()


async def test_disconnect_clears_the_attempt_budget(
    handler: AgoraWebSocketHandler, clock: list[float]
) -> None:
    """A new session must not inherit the previous one's exhausted budget."""
    handler._peer_recover_attempts = handler.PEER_RECOVER_MAX_ATTEMPTS

    await handler.disconnect()

    assert handler._peer_recover_attempts == 0
