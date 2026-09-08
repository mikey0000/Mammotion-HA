"""Tests for the 4G FPV keep-alive loop in ``AgoraWebSocketHandler``.

Over cellular the mower's video encoder stops publishing unless it is re-armed
with ``refresh_fpv`` roughly every 3 s (the app's
``FPV4GVideoStateMannager.refreshInterval = 3000ms``).  ``_fpv_keepalive_loop``
pokes the device on that cadence via the injected ``keepalive`` callback, stops
quietly when the callback reports it isn't needed (WiFi), and ends the stream
when the cloud's free-minutes budget (``availableTime``) is exhausted.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _load_agora_websocket() -> types.ModuleType:
    """Load the real ``agora_websocket.py`` after stubbing its third-party imports.

    Mirrors the loader in ``test_agora_renew_token_debounce.py`` — conftest stubs
    ``custom_components.mammotion`` as a flat module, so relative imports must be
    pre-stubbed and the module loaded by file path.
    """

    def _stub(name: str, **attrs: Any) -> types.ModuleType:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod

    if "sdp_transform" not in sys.modules:
        _stub("sdp_transform", parse=MagicMock())
    if "websockets" not in sys.modules:
        _stub("websockets")
    if "websockets.asyncio" not in sys.modules:
        _stub("websockets.asyncio")
    if "websockets.asyncio.client" not in sys.modules:
        _stub("websockets.asyncio.client", ClientConnection=object, connect=MagicMock())
    if "websockets.exceptions" not in sys.modules:
        _stub("websockets.exceptions", WebSocketException=Exception)
    if "custom_components.mammotion.agora_api" not in sys.modules:
        _stub("custom_components.mammotion.agora_api", AgoraResponse=MagicMock())
    if "custom_components.mammotion.agora_sdp" not in sys.modules:
        _stub("custom_components.mammotion.agora_sdp", parse_offer_to_ortc=MagicMock())
    coord = sys.modules.get("custom_components.mammotion.coordinator")
    if coord is None:
        coord = _stub("custom_components.mammotion.coordinator")
    if not hasattr(coord, "StreamSubscriptionResponse"):
        coord.StreamSubscriptionResponse = MagicMock()

    path = (
        Path(__file__).parent.parent
        / "custom_components"
        / "mammotion"
        / "agora_websocket.py"
    )
    spec = importlib.util.spec_from_file_location(
        "custom_components.mammotion.agora_websocket", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_components.mammotion.agora_websocket"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def agora_module() -> types.ModuleType:
    return _load_agora_websocket()


@pytest.fixture
def handler(agora_module: types.ModuleType) -> Any:
    """Handler with a keep-alive callback wired and a mocked hass."""
    return agora_module.AgoraWebSocketHandler(
        hass=MagicMock(), keepalive=AsyncMock(return_value=True)
    )


def _run(coro: Any) -> Any:
    """Drive a coroutine to completion (the project has no pytest-asyncio)."""
    return asyncio.new_event_loop().run_until_complete(coro)


def test_keepalive_wifi_stops_without_ending_stream(
    handler: Any, agora_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Callback returning False (WiFi) exits after one call; stream left running."""
    handler._keepalive = AsyncMock(return_value=False)
    monkeypatch.setattr(agora_module.asyncio, "sleep", AsyncMock())

    _run(handler._fpv_keepalive_loop(None))

    assert handler._keepalive.await_count == 1
    handler.hass.async_create_task.assert_not_called()


def test_keepalive_4g_pokes_each_interval(
    handler: Any, agora_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On 4G the loop pokes on every interval until the callback stops it."""
    handler._keepalive = AsyncMock(side_effect=[True, True, False])
    sleep_mock = AsyncMock()
    monkeypatch.setattr(agora_module.asyncio, "sleep", sleep_mock)

    _run(handler._fpv_keepalive_loop(None))  # None => no budget deadline

    assert handler._keepalive.await_count == 3
    # Slept once after each of the two successful (True) pokes; not after the
    # final False that ended the loop.
    assert sleep_mock.await_count == 2
    handler.hass.async_create_task.assert_not_called()


def test_keepalive_budget_exhausted_ends_stream(
    handler: Any, agora_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the 4G free-minutes budget elapses, the loop ends the stream."""
    handler._keepalive = AsyncMock(return_value=True)
    handler.disconnect = MagicMock(return_value=None)  # avoid real teardown / coroutine
    monkeypatch.setattr(agora_module.asyncio, "sleep", AsyncMock())
    # First monotonic() computes deadline (0 + 5 = 5); the post-poke check reads
    # 100, which is past the deadline => stream is ended.  Patch the module's
    # ``time`` reference (not the real time module) so asyncio's own clock is
    # untouched.
    times = iter([0.0, 100.0])
    fake_time = types.SimpleNamespace(monotonic=lambda: next(times, 100.0))
    monkeypatch.setattr(agora_module, "time", fake_time)

    _run(handler._fpv_keepalive_loop(5))

    handler._keepalive.assert_awaited_once()
    handler.hass.async_create_task.assert_called_once()


def test_disconnect_cancels_keepalive_task(handler: Any) -> None:
    """disconnect() cancels a running keep-alive task and clears the handle."""

    async def _scenario() -> asyncio.Task:
        async def _runner() -> None:
            await asyncio.sleep(3600)

        task = asyncio.ensure_future(_runner())
        handler._fpv_keepalive_task = task
        handler._websocket = None  # disconnect skips WS teardown
        handler._joined = False
        await handler.disconnect()
        await asyncio.sleep(0)  # let the cancellation propagate
        return task

    task = _run(_scenario())
    assert task.cancelled()
    assert handler._fpv_keepalive_task is None
