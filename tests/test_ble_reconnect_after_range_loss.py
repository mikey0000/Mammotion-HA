"""BLE must come back on its own once the mower is in range again.

Two defects kept it down. The advertisement callback that always runs dropped
the RSSI, and ``BLETransport.is_usable`` fails closed below ``min_rssi`` until a
stronger reading arrives — so a mower that faded out of range stayed unusable
however strongly it came back. And the reconnect attempt sat after the refresh's
``is_online()`` early return, which on a BLE-only mower is False precisely when
BLE is down, so it only ever ran while already connected.
"""

import ast
from pathlib import Path

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"


def _function(src: str, name: str) -> str:
    tree = ast.parse(src)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )
    return ast.get_source_segment(src, node)


def _report_coordinator() -> str:
    src = (_ROOT / "coordinator.py").read_text()
    return src[src.index("class MammotionReportUpdateCoordinator") :]


def test_the_advertisement_callback_carries_the_rssi() -> None:
    """Without it a weak last reading latches the transport unusable for good."""
    body = _function((_ROOT / "__init__.py").read_text(), "_ble_seen")
    assert "rssi=service_info.rssi" in body


def test_reconnect_runs_before_the_refresh_can_bail_out() -> None:
    """``super()._async_update_data()`` returns early while is_online() is False."""
    update = _function(_report_coordinator(), "_async_update_data")
    assert update.index("_async_reconnect_ble()") < update.index(
        "await super()._async_update_data()"
    )


def test_the_advertisement_subscription_is_wired_at_setup() -> None:
    """A mower unreachable at startup never reached the old inline registration."""
    report = _report_coordinator()
    assert "self._async_start()" in _function(report, "_async_setup")
    assert "self._async_start()" in _function(report, "_async_update_data")
    # _async_start used to be dead code duplicated inside the refresh.
    assert report.count("async_register_callback(") == 1


def test_registration_is_idempotent() -> None:
    """It is called from two places now, so it must not stack subscriptions."""
    body = _function(_report_coordinator(), "_async_start")
    assert "if self._on_stop or self.data.mower_state.ble_mac ==" in body


def test_the_subscription_is_dropped_on_shutdown() -> None:
    """_async_stop was never called by anything before."""
    body = _function(_report_coordinator(), "async_shutdown")
    assert "self._async_stop()" in body


def test_reconnect_respects_the_switches_and_the_cooldown() -> None:
    """prefer_ble, the Bluetooth switch, and BLETransport.is_usable all still gate."""
    body = _function(_report_coordinator(), "_async_reconnect_ble")
    assert "self._bluetooth_enabled" in body
    assert "handle.prefer_ble" in body
    assert "ble.is_usable" in body
    assert "ble.is_connected" in body
