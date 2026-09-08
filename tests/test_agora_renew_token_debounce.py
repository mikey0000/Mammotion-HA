"""Tests for the renew_token debounce in ``AgoraWebSocketHandler``.

The 2026-05-22 HA log showed the gateway's ``on_token_privilege_will_expire``
event firing every second for 30 s during the pre-expiry window, with the
handler sending a fresh ``renew_token`` request on every event — 30 sends in
30 s before the token expired anyway.  The debounce caps consecutive sends
at one per ``RENEW_TOKEN_DEBOUNCE_SECS`` window, mirroring how the APK only
needs a single renew per expiry cycle.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _load_agora_websocket() -> types.ModuleType:
    """Load the real ``agora_websocket.py`` after stubbing its third-party imports.

    Conftest stubs ``custom_components.mammotion`` as a flat module rather than
    a package, so the normal import path won't resolve ``.agora_api`` etc.  We
    follow the same ``importlib.util.spec_from_file_location`` pattern conftest
    uses for ``switch.py``.
    """
    # Stub third-party imports agora_websocket touches at module load.
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
    # Relative imports inside the mammotion package
    if "custom_components.mammotion.agora_api" not in sys.modules:
        _stub("custom_components.mammotion.agora_api", AgoraResponse=MagicMock())
    if "custom_components.mammotion.agora_sdp" not in sys.modules:
        _stub("custom_components.mammotion.agora_sdp", parse_offer_to_ortc=MagicMock())
    # coordinator is referenced for StreamSubscriptionResponse type.
    # Conftest may have already stubbed coordinator without that name — set it.
    coord = sys.modules.get("custom_components.mammotion.coordinator")
    if coord is None:
        coord = _stub("custom_components.mammotion.coordinator")
    if not hasattr(coord, "StreamSubscriptionResponse"):
        coord.StreamSubscriptionResponse = MagicMock()

    path = Path(__file__).parent.parent / "custom_components" / "mammotion" / "agora_websocket.py"
    spec = importlib.util.spec_from_file_location("custom_components.mammotion.agora_websocket", path)
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
    """Minimal AgoraWebSocketHandler with WS + agora_data mocked."""
    h = agora_module.AgoraWebSocketHandler(hass=MagicMock())
    h._websocket = AsyncMock()
    h._agora_data = MagicMock()
    h._agora_data.token = "fake-token-abc"
    return h


def _run(coro: Any) -> Any:
    """Drive a coroutine to completion — used because the project doesn't have pytest-asyncio."""
    return asyncio.new_event_loop().run_until_complete(coro)


def test_renew_token_first_call_sends(handler: Any) -> None:
    """First renew_token call goes through; ``_websocket.send`` is invoked once."""
    _run(handler._send_renew_token())
    assert handler._websocket.send.await_count == 1
    payload = json.loads(handler._websocket.send.await_args.args[0])
    assert payload["_type"] == "renew_token"
    assert payload["_message"]["token"] == "fake-token-abc"


def test_renew_token_storm_is_debounced(handler: Any) -> None:
    """30 back-to-back renew_token calls (simulating the gateway's
    once-per-second pre-expiry storm) produce only ONE WS send."""
    async def _storm() -> None:
        for _ in range(30):
            await handler._send_renew_token()
    _run(_storm())

    assert handler._websocket.send.await_count == 1, (
        f"Expected 1 send after debounce; got {handler._websocket.send.await_count}.  "
        f"The renew_token debounce regressed and the storm is back."
    )


def test_renew_token_passes_after_did_expire(handler: Any) -> None:
    """When the gateway emits ``on_token_privilege_did_expire``, the debounce
    is reset (``_last_renew_token_at = 0``) and the next renew goes through."""
    async def _scenario() -> None:
        await handler._send_renew_token()  # 1: accepted
        await handler._send_renew_token()  # 2: suppressed
        handler._last_renew_token_at = 0.0  # simulate did_expire reset
        await handler._send_renew_token()  # 3: accepted again
    _run(_scenario())
    assert handler._websocket.send.await_count == 2


def test_renew_token_failed_send_clears_debounce(handler: Any) -> None:
    """If the send raises ConnectionError, the debounce is reset so the next
    event can retry instead of waiting 30 s for a send that never went out."""
    handler._websocket.send.side_effect = ConnectionError("ws closed")

    async def _scenario() -> None:
        await handler._send_renew_token()
        # Send was attempted, then failed → debounce cleared
        assert handler._last_renew_token_at == 0.0
        # Reset the side effect; next renew should go through immediately.
        handler._websocket.send.side_effect = None
        await handler._send_renew_token()
    _run(_scenario())
    assert handler._websocket.send.await_count == 2


def test_renew_token_no_op_when_no_websocket(handler: Any) -> None:
    """If _websocket or _agora_data is None, _send_renew_token is a no-op."""
    handler._websocket = None
    _run(handler._send_renew_token())  # must not raise
    # Restore a websocket but drop agora_data
    handler._websocket = AsyncMock()
    handler._agora_data = None
    _run(handler._send_renew_token())
    assert handler._websocket.send.await_count == 0


def test_concurrent_renew_calls_only_send_once(handler: Any) -> None:
    """asyncio.gather'ing many concurrent _send_renew_token calls still only
    produces one WS send — the debounce is checked before the send."""
    async def _gather() -> None:
        await asyncio.gather(*(handler._send_renew_token() for _ in range(10)))
    _run(_gather())
    # Tolerate a small race window: at most a couple sends, but well under 10.
    assert handler._websocket.send.await_count <= 2, (
        f"Concurrent renew calls bypassed the debounce; got {handler._websocket.send.await_count} sends"
    )
