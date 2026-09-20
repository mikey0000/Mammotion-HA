"""No coordinator talks to a device whose updates switch is off.

The report coordinator was fixed for #889, but its three siblings —
maintenance, version and errors — each sent their one-off reads from
``_async_setup`` regardless, so a device set up with updates off was still
probed four ways on every Home Assistant start.

The reads now sit behind a shared hook.  ``_async_setup`` keeps doing its
wiring either way, because it runs once per session and an early return there
would strand the ``sys_status`` watches until a config-entry reload.
"""

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_COORDINATORS = [
    "MammotionReportUpdateCoordinator",
    "MammotionMaintenanceUpdateCoordinator",
    "MammotionDeviceVersionUpdateCoordinator",
    "MammotionDeviceErrorUpdateCoordinator",
]

#: Sends that used to go out from _async_setup unconditionally.
_PROBES = {
    "MammotionReportUpdateCoordinator": "send_todev_ble_sync",
    "MammotionMaintenanceUpdateCoordinator": "get_maintenance",
    "MammotionDeviceVersionUpdateCoordinator": "get_device_version_main",
    "MammotionDeviceErrorUpdateCoordinator": "read_write_device",
}


def _class_source(name: str) -> str:
    src = (_ROOT / "coordinator.py").read_text()
    tree = ast.parse(src)
    node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name
    )
    return ast.get_source_segment(src, node)


def _method(class_source: str, name: str) -> str | None:
    tree = ast.parse(class_source)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name == name
        ):
            return ast.get_source_segment(class_source, node)
    return None


@pytest.mark.parametrize("coordinator", _COORDINATORS)
def test_setup_defers_its_reads_to_the_gated_hook(coordinator: str) -> None:
    """The probes must not be reachable straight from _async_setup."""
    source = _class_source(coordinator)
    setup = _method(source, "_async_setup")
    assert setup is not None
    assert "_async_ensure_startup_reads()" in setup
    assert _PROBES[coordinator] not in setup


@pytest.mark.parametrize("coordinator", _COORDINATORS)
def test_each_one_owns_a_startup_reads_hook(coordinator: str) -> None:
    """The probes moved into the hook rather than being dropped."""
    source = _class_source(coordinator)
    reads = _method(source, "_async_startup_reads")
    assert reads is not None, coordinator
    assert _PROBES[coordinator] in reads


def test_the_gate_checks_the_device_and_runs_once() -> None:
    """Off means silent; on means exactly one run, not one per refresh."""
    base = _class_source("MammotionBaseUpdateCoordinator")
    gate = _method(base, "_async_ensure_startup_reads")
    assert gate is not None
    assert "if self._startup_reads_done:" in gate
    assert "not device.enabled" in gate
    assert "self._startup_reads_done = True" in gate


def test_a_sibling_catches_up_on_its_first_enabled_refresh() -> None:
    """The switch only reaches the report coordinator, so the others self-heal."""
    base = _class_source("MammotionBaseUpdateCoordinator")
    update = _method(base, "_async_update_data")
    assert update is not None
    gate = update.index("_async_ensure_startup_reads()")
    assert update.index("if not device.enabled:") < gate


@pytest.mark.parametrize("coordinator", _COORDINATORS)
def test_the_one_time_wiring_still_happens_when_disabled(coordinator: str) -> None:
    """An early return here would strand it until a reload — the #889 trap."""
    setup = _method(_class_source(coordinator), "_async_setup")
    assert setup is not None
    assert (
        "return" not in setup
        or "watch_field" not in setup
        or setup.index("watch_field") < setup.index("_async_ensure_startup_reads()")
    )
