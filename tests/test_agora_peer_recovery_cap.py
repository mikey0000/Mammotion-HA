"""Tests for the bounded peer-recovery loop in ``AgoraWebSocketHandler``.

When the mower drops out of the Agora channel the handler re-requests the
stream so the session survives a transient blip.  Without a cap that retry is
unconditional: a mower that keeps rejoining and quitting (the ~60 s
``Quit`` / ``ServerTimeOut`` cycle seen in the wild) gets resurrected forever,
so an unwatched stream runs until Home Assistant restarts.  These tests pin the
attempt budget, the window that forgives an occasional drop, and the reset on
session boundaries.
"""

from __future__ import annotations

import asyncio
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_fpv_keepalive import _load_agora_websocket


@pytest.fixture(scope="module")
def agora_module() -> types.ModuleType:
    """Load the real handler module once per test module."""
    return _load_agora_websocket()


# Well past PEER_RECOVER_COOLDOWN_SECS so the first recovery isn't itself
# suppressed by the cooldown (which compares against an initial 0.0).
CLOCK_BASE = 10_000.0


@pytest.fixture
def clock(
    agora_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> list[float]:
    """Settable monotonic clock; element 0 is the current reading."""
    now = [CLOCK_BASE]
    monkeypatch.setattr(agora_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(agora_module.asyncio, "sleep", AsyncMock())
    return now


@pytest.fixture
def handler(agora_module: types.ModuleType) -> Any:
    """Build a handler wired for recovery, 'connected' with the peer gone."""
    h = agora_module.AgoraWebSocketHandler(hass=MagicMock(), recover_stream=AsyncMock())
    h._websocket = MagicMock()
    h._connection_state = "CONNECTED"
    return h


def _recover(handler: Any, clock: list[float], at: float) -> None:
    """Run one ``_peer_recovery`` pass with the clock at ``CLOCK_BASE + at``."""
    clock[0] = CLOCK_BASE + at
    asyncio.new_event_loop().run_until_complete(handler._peer_recovery(1))


def test_recovery_stops_at_the_attempt_cap(handler: Any, clock: list[float]) -> None:
    """A mower that never settles is retried a bounded number of times."""
    cap = handler.PEER_RECOVER_MAX_ATTEMPTS
    # Space each drop past the cooldown but well inside the reset window, which
    # is the thrash pattern the cap exists to stop.
    step = handler.PEER_RECOVER_COOLDOWN_SECS + 1.0
    assert step * (cap + 3) < handler.PEER_RECOVER_RESET_SECS

    for i in range(cap + 3):
        _recover(handler, clock, at=i * step)

    assert handler._recover_stream.await_count == cap


def test_settled_stream_forgives_an_occasional_drop(
    handler: Any, clock: list[float]
) -> None:
    """A drop after a long healthy run starts a fresh budget."""
    cap = handler.PEER_RECOVER_MAX_ATTEMPTS
    step = handler.PEER_RECOVER_COOLDOWN_SECS + 1.0
    for i in range(cap):
        _recover(handler, clock, at=i * step)
    assert handler._recover_stream.await_count == cap

    # Another drop inside the reset window: still capped.
    _recover(handler, clock, at=cap * step)
    assert handler._recover_stream.await_count == cap

    # A drop after a long quiet period means the stream had settled.
    _recover(handler, clock, at=cap * step + handler.PEER_RECOVER_RESET_SECS + 1)
    assert handler._recover_stream.await_count == cap + 1


def test_cooldown_still_suppresses_rapid_retries(
    handler: Any, clock: list[float]
) -> None:
    """Back-to-back drops inside the cooldown don't burn the budget."""
    _recover(handler, clock, at=0.0)
    _recover(handler, clock, at=1.0)

    assert handler._recover_stream.await_count == 1
    assert handler._peer_recover_attempts == 1


def test_no_recovery_once_the_viewer_is_gone(handler: Any, clock: list[float]) -> None:
    """A closed websocket means nobody is watching — never re-request."""
    handler._websocket = None

    _recover(handler, clock, at=0.0)

    handler._recover_stream.assert_not_awaited()


def test_peer_rejoining_cancels_the_recovery(handler: Any, clock: list[float]) -> None:
    """A peer back online within the debounce window needs no recovery."""
    handler._online_users.add(1)

    _recover(handler, clock, at=0.0)

    handler._recover_stream.assert_not_awaited()


def test_disconnect_clears_the_attempt_budget(handler: Any, clock: list[float]) -> None:
    """A new session must not inherit the previous one's exhausted budget."""
    handler._peer_recover_attempts = handler.PEER_RECOVER_MAX_ATTEMPTS

    asyncio.new_event_loop().run_until_complete(handler.disconnect())

    assert handler._peer_recover_attempts == 0
