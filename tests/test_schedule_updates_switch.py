"""Turning "Enable updates" off must not take the device offline (issue #889).

The switch used to call ``MammotionClient.set_scheduled_updates``, which
disconnects BLE and detaches the account's cloud transports from the handle.
That dropped both legs of ``MammotionBaseEntity.available``, so every entity of
the device — the switch itself included — went unavailable with no way back
short of reloading the config entry, and the position was never written to disk
so the reload brought it back on.
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


def _class(src: str, name: str) -> ast.ClassDef:
    tree = ast.parse(src)
    return next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name
    )


def test_the_switch_no_longer_touches_transports() -> None:
    """Transport state belongs to the Bluetooth and Cloud switches alone."""
    body = _function((_ROOT / "coordinator.py").read_text(), "set_scheduled_updates")
    assert "manager.set_scheduled_updates" not in body
    assert "set_cloud_attached" not in body
    assert "stop_polling()" in body
    assert "resume_polling()" in body


def test_the_position_is_flushed_to_disk_when_it_changes() -> None:
    """Polling stops on disable, so nothing else would ever save the snapshot."""
    body = _function((_ROOT / "coordinator.py").read_text(), "set_scheduled_updates")
    assert "async_save_data(device)" in body
    assert "async_flush_saved_data()" in body
    # The inbound MQTT handlers re-assert True on every frame; an unconditional
    # flush there would hit the disk once per message.
    assert "changed = device.enabled != enabled" in body
    assert "if changed:" in body


def test_a_restored_disabled_device_stays_quiet() -> None:
    """``enabled`` round-trips through the device snapshot, so setup must honour it."""
    src = (_ROOT / "coordinator.py").read_text()
    report = src[src.index("class MammotionReportUpdateCoordinator") :]
    setup = _function(report, "_async_setup")
    guard = setup.index("if not self.data.enabled:")
    assert "stop_polling()" in setup[guard:]
    # The startup reads are skipped, not run and discarded.
    assert "_async_ensure_startup_reads()" in setup
    assert "send_todev_ble_sync" not in setup


def test_setup_still_wires_the_watch_when_updates_are_off() -> None:
    """_async_setup runs once a session; an early return stranded it till a reload."""
    src = (_ROOT / "coordinator.py").read_text()
    report = src[src.index("class MammotionReportUpdateCoordinator") :]
    setup = _function(report, "_async_setup")
    assert "watch_field" in setup
    assert setup.index("if not self.data.enabled:") < setup.index("watch_field")
    assert "return" not in setup


def test_re_enabling_runs_the_reads_setup_skipped() -> None:
    """Otherwise the settings entities sit on defaults until the entry reloads."""
    src = (_ROOT / "coordinator.py").read_text()
    report = src[src.index("class MammotionReportUpdateCoordinator") :]
    body = _function(report, "set_scheduled_updates")
    assert "if changed and enabled and" in body
    assert "_async_ensure_startup_reads()" in body


def test_enabling_resumes_polling_rather_than_only_restarting_keepalive() -> None:
    """restart_keep_alive honours the stop this switch set, so it alone never returns."""
    src = (_ROOT / "coordinator.py").read_text()
    base = src[: src.index("class MammotionReportUpdateCoordinator")]
    body = _function(base, "set_scheduled_updates")
    assert "resume_polling()" in body
    assert "restart_keep_alive()" not in body


def test_the_switch_cannot_strand_itself() -> None:
    """It is the only way back from updates-off, so it overrides the base check."""
    src = (_ROOT / "switch.py").read_text()
    node = _class(src, "MammotionUpdateSwitchEntity")
    available = next(
        n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "available"
    )
    body = ast.get_source_segment(src, available)
    assert "self.coordinator.data is not None" in body
    assert "is_online" not in body


def test_reconnecting_ble_respects_the_updates_switch() -> None:
    """A BLE reconnect restarts the device's stream, undoing the stop."""
    src = (_ROOT / "coordinator.py").read_text()
    report = src[src.index("class MammotionReportUpdateCoordinator") :]
    body = _function(report, "_async_reconnect_ble")
    assert "not self.data.enabled" in body


def test_the_sys_status_watch_stays_quiet_while_off() -> None:
    """The watch is wired either way, so the callback is what has to check."""
    src = (_ROOT / "coordinator.py").read_text()
    report = src[src.index("class MammotionReportUpdateCoordinator") :]
    body = _function(report, "_on_sys_status_changed_refresh")
    assert body.index("if not self.data.enabled:") < body.index("try:")


def test_the_startup_reads_do_not_block_the_service_call() -> None:
    """They carry a 60s budget; awaiting them would hold switch.turn_on open."""
    src = (_ROOT / "coordinator.py").read_text()
    report = src[src.index("class MammotionReportUpdateCoordinator") :]
    body = _function(report, "set_scheduled_updates")
    assert "async_create_background_task" in body
    assert "await self._async_ensure_startup_reads()" not in body


def test_the_transition_comes_from_the_base_not_a_second_read() -> None:
    """The base bails out on a missing device/handle; the override must see that."""
    src = (_ROOT / "coordinator.py").read_text()
    base = src[: src.index("class MammotionReportUpdateCoordinator")]
    assert "async def set_scheduled_updates(self, enabled: bool) -> bool:" in base
    report = src[src.index("class MammotionReportUpdateCoordinator") :]
    body = _function(report, "set_scheduled_updates")
    assert "changed = await super().set_scheduled_updates(enabled)" in body
    assert "self.data.enabled" not in body
