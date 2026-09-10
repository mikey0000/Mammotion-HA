"""Pool cleaner coming online re-arms its status stream so the entity leaves 'unavailable'.

The Spino only pushes report frames after ``get_report_cfg_spino`` (issued once at
setup). If it was offline then, a later thing/status CONNECTED must re-subscribe or
``PoolStateReducer`` never flips ``online`` and the entity stays unavailable — the
regression this covers (worked on 0.6.4).
"""

import ast
import contextlib
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_COORD = Path(__file__).parent.parent / "custom_components" / "mammotion" / "coordinator.py"
_METHODS = ("_async_update_status", "_async_resubscribe_status")


class _StatusType:
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


def _bound(*, connected: bool, online: bool) -> types.SimpleNamespace:
    src = _COORD.read_text()
    tree = ast.parse(src)
    segs = {
        n.name: ast.get_source_segment(src, n)
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name in _METHODS
    }
    assert set(segs) == set(_METHODS), segs.keys()
    ns: dict = {
        "contextlib": contextlib,
        "StatusType": _StatusType,
        "GatewayTimeoutException": type("GatewayTimeoutException", (Exception,), {}),
        "NoTransportAvailableError": type("NoTransportAvailableError", (Exception,), {}),
        "DeviceOfflineException": type("DeviceOfflineException", (Exception,), {}),
    }
    for name in _METHODS:
        exec(compile(segs[name], "<coordinator>", "exec"), ns)  # noqa: S102

    created: list = []
    coordinator = types.SimpleNamespace(
        data=types.SimpleNamespace(online=online),
        async_subscribe_status=AsyncMock(),
        hass=types.SimpleNamespace(async_create_task=lambda coro: created.append(coro)),
    )
    coordinator._created = created
    for name in _METHODS:
        setattr(coordinator, name, types.MethodType(ns[name], coordinator))
    status = types.SimpleNamespace(
        params=types.SimpleNamespace(
            status=types.SimpleNamespace(
                value=_StatusType.CONNECTED if connected else _StatusType.DISCONNECTED
            )
        )
    )
    return coordinator, status


@pytest.mark.anyio
async def test_reconnect_while_offline_resubscribes() -> None:
    coordinator, status = _bound(connected=True, online=False)
    await coordinator._async_update_status(status)
    assert len(coordinator._created) == 1  # a resubscribe task was scheduled
    await coordinator._created[0]  # drive it
    coordinator.async_subscribe_status.assert_awaited_once()


@pytest.mark.anyio
async def test_connected_while_already_online_does_not_resubscribe() -> None:
    coordinator, status = _bound(connected=True, online=True)
    await coordinator._async_update_status(status)
    assert coordinator._created == []


@pytest.mark.anyio
async def test_disconnected_status_does_nothing() -> None:
    coordinator, status = _bound(connected=False, online=False)
    await coordinator._async_update_status(status)
    assert coordinator._created == []


@pytest.mark.anyio
async def test_resubscribe_swallows_a_flaky_transport() -> None:
    coordinator, _ = _bound(connected=True, online=False)
    exc = coordinator._async_resubscribe_status.__func__.__globals__["DeviceOfflineException"]
    coordinator.async_subscribe_status = AsyncMock(side_effect=exc())
    await coordinator._async_resubscribe_status()  # must not raise


def test_spino_coordinator_overrides_status_handler() -> None:
    src = _COORD.read_text()
    spino = src[src.index("class MammotionSpinoCoordinator") :]
    assert "async def _async_update_status" in spino
    assert "async_subscribe_status" in spino[: spino.index("async def _async_resubscribe_status") + 200]
