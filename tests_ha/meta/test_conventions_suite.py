"""The suite's own hygiene (docs/testing.md §14.10), asserted on the test modules' source.

A baseline is a ratchet: exceeding an entry fails, and so does undershooting
one.  The text rules see string literals too, which is why this one module is
exempt from the scan: it spells out every construct it bans.
"""

from __future__ import annotations

import ast
import collections
import re
from pathlib import Path

from .conventions_support import TESTS, assert_frozen, assert_ratchet

SELF = Path(__file__).resolve()
MAX_MODULE_LINES = 600
BUILDER_NAME = re.compile(r"_?make_[a-z0-9_]+")
SKIP_MARKS = ("pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail")

#: ``sleep(<non-zero literal>)`` calls, per module.
WALL_CLOCK_SLEEPS: dict[str, int] = {"test_ble_first_setup.py": 1}

#: Bare ``@pytest.mark.asyncio`` markers, redundant under ``asyncio_mode = "auto"``.
BARE_ASYNCIO_MARKER: dict[str, int] = {}

#: Test modules over the size cap, at the length they had when the cap landed.
OVERSIZED: dict[str, int] = {
    "test_notifications.py": 734,
    "test_switch_area_lifecycle.py": 604,
}

#: Test parameters without a type annotation, per module.
UNANNOTATED_PARAMETERS: dict[str, int] = {
    "test_geojson_offset.py": 6,
    "test_services_yaml.py": 7,
    "test_wifi_movement.py": 3,
}

#: ``MagicMock(...)`` with no ``spec`` / ``spec_set``, per module.
UNSPECCED_MAGICMOCKS: dict[str, int] = {
    "area_switch_support.py": 1,
    "test_auto_change_direction_gate.py": 4,
    "test_blade_height_initial_value.py": 5,
    "test_ble_advertisement_subscription.py": 6,
    "test_ble_first_setup.py": 18,
    "test_ble_reconnect_after_range_loss.py": 16,
    "test_button_tasks.py": 4,
    "test_bypass_mode_options.py": 4,
    "test_camera_agora_session.py": 4,
    "test_camera_ble_only.py": 3,
    "test_camera_session_tracking.py": 10,
    "test_camera_webrtc_teardown.py": 16,
    "test_capabilities.py": 4,
    "test_config_flow_accounts.py": 1,
    "test_config_flow_ble_merge.py": 1,
    "test_config_flow_user_step.py": 1,
    "test_continue_last_job_button.py": 2,
    "test_coordinator_area_name.py": 4,
    "test_diagnostics.py": 3,
    "test_disabled_updates_stay_quiet.py": 6,
    "test_dropmow_button.py": 1,
    "test_edit_task_keeps_enabled.py": 5,
    "test_event_notification.py": 2,
    "test_fetch_mow_path.py": 5,
    "test_firmware_check_on_reconnect.py": 8,
    "test_firmware_gated_entities.py": 3,
    "test_get_task.py": 3,
    "test_grass_collection_gate.py": 10,
    "test_grass_collection_switches.py": 1,
    "test_job_service_route_fields.py": 4,
    "test_map_backup.py": 4,
    "test_mower_target_services.py": 2,
    "test_notifications.py": 3,
    "test_options_flow_prefer_ble.py": 1,
    "test_restore_salvage.py": 3,
    "test_ride_boundary_distance.py": 5,
    "test_rtk_device_info.py": 3,
    "test_running_task.py": 7,
    "test_schedule_auto_change_direction.py": 3,
    "test_schedule_ride_boundary_distance.py": 3,
    "test_schedule_toward_fields.py": 3,
    "test_schedule_updates_switch.py": 12,
    "test_spino_device_identity.py": 7,
    "test_spino_device_info.py": 1,
    "test_spino_work_modes.py": 10,
    "test_status_handler_transport_error.py": 7,
    "test_stream_session.py": 3,
    "test_svg_update_keeps_tile.py": 3,
    "test_switch_cloud_ble_only.py": 3,
    "test_task_area_sensors.py": 1,
    "test_task_enabled_readback.py": 8,
    "test_transport_switch_restore.py": 8,
    "test_unload_credentials.py": 6,
    "test_wildlife_safety_gate.py": 4,
}

#: Builders defined in more than one module instead of a ``*_support.py``.
DUPLICATE_BUILDERS: dict[str, tuple[str, ...]] = {
    "make_coordinator": (
        "area_switch_support.py",
        "test_coordinator_bring_up.py",
        "test_error_log.py",
        "user_command_support.py",
    ),
}

#: Modules with no docstring.
UNDOCUMENTED_MODULES: frozenset[str] = frozenset()


def _python_files() -> list[Path]:
    """Every module under tests_ha except this one."""
    return sorted(
        p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts and p.resolve() != SELF
    )


def _test_modules() -> list[Path]:
    return [p for p in _python_files() if p.name.startswith("test_")]


def _rel(path: Path) -> str:
    return path.relative_to(TESTS).as_posix()


def _tests(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")
    ]


def test_nothing_calls_time_sleep() -> None:
    """A blocking sleep stalls the event loop and the whole suite (§6)."""
    offenders = [_rel(p) for p in _python_files() if re.search(r"\btime\.sleep\(", p.read_text())]
    assert not offenders, offenders


def test_nothing_sleeps_on_the_wall_clock() -> None:
    """A fixed sleep is a hope, not a synchronisation (§6)."""
    counts = collections.Counter(
        _rel(path)
        for path in _python_files()
        for value in re.findall(r"\b\w*sleep\(\s*([0-9][0-9.]*)", path.read_text())
        if float(value) > 0
    )
    assert_ratchet(
        dict(counts), WALL_CLOCK_SLEEPS, "WALL_CLOCK_SLEEPS", "wait on an event or move the clock"
    )


def test_no_bare_asyncio_marker() -> None:
    """``asyncio_mode = "auto"`` already applies it (§6)."""
    counts = {
        _rel(p): n
        for p in _python_files()
        if (n := len(re.findall(r"pytest\.mark\.asyncio(?!\s*\()", p.read_text())))
    }
    assert_ratchet(counts, BARE_ASYNCIO_MARKER, "BARE_ASYNCIO_MARKER", "delete the decorator")


def test_nothing_prints_or_runs_as_a_script() -> None:
    """A test reports through assertions, and pytest is its only entry point (§8)."""
    offenders = [
        _rel(p)
        for p in _python_files()
        if re.search(r"(^|[^.\w])print\(", p.read_text())
        or re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', p.read_text())
    ]
    assert not offenders, offenders


def _states_a_reason(decorator: ast.expr, text: str) -> bool:
    """Whether a skip/xfail decorator names its blocker; ``skip`` takes it positionally too."""
    if not isinstance(decorator, ast.Call):
        return False
    if any(keyword.arg == "reason" for keyword in decorator.keywords):
        return True
    return bool(
        text.startswith("pytest.mark.skip(")
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
    )


def test_skips_and_expected_failures_state_a_reason() -> None:
    """A skip with no reason becomes permanent (§8)."""
    unexplained = [
        f"{_rel(module)}::{node.name}"
        for module in _test_modules()
        for node in _tests(ast.parse(module.read_text()))
        for decorator in node.decorator_list
        if (text := ast.unparse(decorator)).startswith(SKIP_MARKS)
        and not _states_a_reason(decorator, text)
    ]
    assert not unexplained, unexplained


def test_every_module_has_a_docstring() -> None:
    """The docstring says what surface the module covers (§3)."""
    missing = {
        _rel(p)
        for p in _python_files()
        if p.name != "__init__.py" and ast.get_docstring(ast.parse(p.read_text())) is None
    }
    assert_frozen(missing, UNDOCUMENTED_MODULES, "UNDOCUMENTED_MODULES", "add a module docstring")


def test_test_modules_stay_under_the_size_cap() -> None:
    """Past the cap, split by concern — ``test_<module>_<concern>.py`` (§2)."""
    lengths = {
        _rel(p): n
        for p in _test_modules()
        if (n := len(p.read_text().splitlines())) > MAX_MODULE_LINES
    }
    assert_ratchet(lengths, OVERSIZED, "OVERSIZED", f"split by concern (cap {MAX_MODULE_LINES} lines)")


def test_every_test_parameter_is_annotated() -> None:
    """The concrete type documents the fixture (§10)."""
    counts = collections.Counter(
        _rel(module)
        for module in _test_modules()
        for node in _tests(ast.parse(module.read_text()))
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if arg.annotation is None and arg.arg != "self"
    )
    assert_ratchet(
        dict(counts), UNANNOTATED_PARAMETERS, "UNANNOTATED_PARAMETERS", "annotate the parameter"
    )


def test_magicmocks_are_specced() -> None:
    """A bare ``MagicMock()`` answers every attribute, so a renamed method still passes (§5)."""
    counts = collections.Counter(
        _rel(path)
        for path in _python_files()
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name | ast.Attribute)
        and ast.unparse(node.func).rsplit(".", 1)[-1] == "MagicMock"
        and not node.args
        and not {"spec", "spec_set"} & {k.arg for k in node.keywords}
    )
    assert_ratchet(
        dict(counts), UNSPECCED_MAGICMOCKS, "UNSPECCED_MAGICMOCKS", "pass spec= (or use create_autospec)"
    )


def test_builders_are_not_duplicated_across_modules() -> None:
    """A builder a second module needs moves to a ``*_support.py`` (§2, §4)."""
    where: dict[str, list[str]] = collections.defaultdict(list)
    for module in _python_files():
        for node in ast.parse(module.read_text()).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and BUILDER_NAME.fullmatch(
                node.name
            ):
                where[node.name].append(_rel(module))
    duplicated = {name: tuple(sorted(paths)) for name, paths in where.items() if len(paths) > 1}
    assert duplicated == DUPLICATE_BUILDERS, (
        "DUPLICATE_BUILDERS (docs/testing.md §14.10): promote a new duplicate to a *_support.py, "
        f"and record one that was merged: {duplicated}"
    )


def test_regression_tests_document_the_defect() -> None:
    """A regression test's docstring records what the code did wrong (§7)."""
    undocumented = [
        f"{_rel(module)}::{node.name}"
        for module in _test_modules()
        for node in _tests(ast.parse(module.read_text()))
        if any(ast.unparse(d).startswith("pytest.mark.regression") for d in node.decorator_list)
        and not ast.get_docstring(node)
    ]
    assert not undocumented, undocumented
