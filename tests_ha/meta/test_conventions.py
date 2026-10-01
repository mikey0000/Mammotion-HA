"""How the integration handles failures (docs/testing.md §14.3–§14.6), asserted on its source.

Each rule runs over the AST, with no ``hass``.  A baseline is a ratchet:
exceeding an entry fails, and so does undershooting one, so an improvement must
be recorded by tightening the entry.  §14.1–§14.2 live in
``test_conventions_schemas.py``, §14.7–§14.9 in ``test_conventions_tables.py``
and §14.10 in ``test_conventions_suite.py``; ``pytest tests_ha/meta`` runs them all.
"""

from __future__ import annotations

import ast
import collections
import types
from pathlib import Path

from homeassistant.exceptions import HomeAssistantError

from custom_components.mammotion.const import COMMAND_EXCEPTIONS

from .conventions_support import (
    DEFERRED,
    INTEGRATION,
    assert_ratchet,
    dotted,
    functions,
    integration_module,
    own_nodes,
    parents,
    parse,
    rel,
    resolve,
    source_files,
)

#: The setup paths where a catch-all must not turn a failure into a value (§14.4).
SETUP_MODULES = ("__init__.py", "coordinator.py")

#: Catch-alls in the setup paths that may return a value, and why (§14.4).
CATCH_ALL_ALLOWED: dict[str, str] = {
    "custom_components/mammotion/coordinator.py::MammotionBaseUpdateCoordinator.async_check_stream_expiry": (
        "camera offer path, not setup: no token answers the viewer with an error (camera.py)"
    ),
}

#: Catch-alls in the setup paths returning a value, per function, when the rule landed.
CATCH_ALL_RETURNS: dict[str, int] = {}

#: ``except ...: pass`` in code an entity action reaches, per function.
SWALLOWED_IN_ACTIONS: dict[str, int] = {}

#: Entity action awaits that can raise a raw library exception, per method (§14.5).
UNMAPPED_ACTION_CALLS: dict[str, int] = {
    "custom_components/mammotion/button.py::press_fn=coordinator.async_cancel_task": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_confirm_remote_drive": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_continue_last_job": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_leave_dock": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_move_back": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_move_forward": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_move_left": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_move_right": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_start_no_area_work": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_sync_maps": 1,
    "custom_components/mammotion/button.py::press_fn=coordinator.async_sync_tasks": 1,
    "custom_components/mammotion/services.py::async_setup_services.handle_fetch_mow_path": 1,
    "custom_components/mammotion/services.py::async_setup_services.handle_svg_add": 1,
    "custom_components/mammotion/services.py::async_setup_services.handle_svg_delete": 1,
    "custom_components/mammotion/services.py::async_setup_services.handle_svg_update": 1,
    "custom_components/mammotion/switch.py::MammotionRemoteDriveSwitchEntity.async_turn_off": 1,
    "custom_components/mammotion/switch.py::set_fn=coordinator.async_set_bluetooth_enabled": 1,
    "custom_components/mammotion/switch.py::set_fn=coordinator.async_set_cloud_enabled": 1,
    "custom_components/mammotion/update.py::MammotionRTKUpdateEntity.async_install": 1,
    "custom_components/mammotion/update.py::MammotionSpinoUpdateEntity.async_install": 1,
    "custom_components/mammotion/update.py::MammotionUpdateEntity.async_install": 1,
}

#: ``raise HomeAssistantError`` / ``ServiceValidationError`` without both translation kwargs.
UNTRANSLATED_RAISES: dict[str, int] = {"custom_components/mammotion/coordinator.py": 1}

#: Tuples outside const.py holding three or more ``COMMAND_EXCEPTIONS`` members (§14.6).
COPIED_EXCEPTION_TUPLES: dict[str, int] = {}

#: HA entity methods that run a user's action.
ACTION_METHODS = frozenset(
    {
        "async_press",
        "async_turn_on",
        "async_turn_off",
        "async_toggle",
        "async_select_option",
        "async_set_native_value",
        "async_set_value",
        "async_start_mowing",
        "async_dock",
        "async_pause",
        "async_install",
        "async_start",
        "async_stop",
        "async_return_to_base",
        "async_clean_spot",
        "async_locate",
        "async_send_command",
    }
)

#: Raised by an action as its translated failure.
_HA_ERRORS = frozenset({"HomeAssistantError", "ServiceValidationError"})


def _path(name: str) -> Path:
    return INTEGRATION / name


def _qualname(path: Path, function: ast.AST) -> str:
    """Return ``custom_components/mammotion/x.py::Class.method``."""
    names = []
    node: ast.AST | None = function
    while node is not None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
        node = parents(path).get(node)
    return f"{rel(path)}::{'.'.join(reversed(names))}"


def _enclosing_function(path: Path, node: ast.AST) -> ast.AST | None:
    while (node := parents(path).get(node)) is not None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node
    return None


def _raises_ha_error(statements: list[ast.stmt], module: types.ModuleType) -> bool:
    """Whether *statements* raise a ``HomeAssistantError`` subclass."""
    for node in ast.walk(ast.Module(body=statements, type_ignores=[])):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            raised = resolve(node.exc.func, module)
            if raised and all(
                isinstance(cls, type) and issubclass(cls, HomeAssistantError)
                for cls in raised
            ):
                return True
    return False


def _maps_command_failures(handler: ast.ExceptHandler, module: types.ModuleType) -> bool:
    """Whether *handler* catches every ``COMMAND_EXCEPTIONS`` member and raises a translated error."""
    caught = resolve(handler.type, module) or ()
    covered = all(any(issubclass(cls, c) for c in caught) for cls in COMMAND_EXCEPTIONS)
    return covered and _raises_ha_error(handler.body, module)


def _guarded(path: Path, node: ast.AST, module: types.ModuleType) -> bool:
    """Whether *node* sits in a ``try`` body whose handler maps command failures."""
    child = node
    while (parent := parents(path).get(child)) is not None:
        if isinstance(parent, DEFERRED):
            return False
        if (
            isinstance(parent, ast.Try | ast.TryStar)
            and child in parent.body
            and any(_maps_command_failures(h, module) for h in parent.handlers)
        ):
            return True
        child = parent
    return False


def _is_raw_library_call(call: ast.expr) -> bool:
    """Whether *call* reaches pymammotion or the cloud directly, past the coordinator's mapping."""
    if not isinstance(call, ast.Call):
        return False
    chain = dotted(call.func)
    return ".manager." in f".{chain}" or chain.startswith(("http.", "self._http."))


def _unmapped_coordinator_methods() -> frozenset[str]:
    """Return the coordinator methods that can raise a raw library exception.

    A method is unmapped when it awaits ``self.manager.*`` (or the cloud HTTP
    client) outside a ``try`` that maps command failures, or awaits another
    unmapped ``self.*`` method that way.  Nested callbacks are left out: they are
    handed to ``_async_device_call``, which is where the mapping lives.
    """
    path = _path("coordinator.py")
    module = integration_module(path)
    raw: set[str] = set()
    calls: dict[str, set[str]] = collections.defaultdict(set)
    for function in functions(parse(path)):
        if not isinstance(parents(path).get(function), ast.ClassDef):
            continue
        for node in own_nodes(function):
            if not isinstance(node, ast.Await) or _guarded(path, node, module):
                continue
            if _is_raw_library_call(node.value):
                raw.add(function.name)
            elif isinstance(node.value, ast.Call) and (
                target := dotted(node.value.func)
            ).startswith("self."):
                calls[function.name].add(target.removeprefix("self."))
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            if name not in raw and callees & raw:
                raw.add(name)
                changed = True
    return frozenset(raw)


def _platform_action_functions() -> list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Return every entity action method and service handler in the platforms."""
    registered: set[str] = set()
    for path in source_files():
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.keyword) and node.arg == "func" and isinstance(
                node.value, ast.Constant
            ):
                registered.add(node.value.value)
    found = []
    for path in source_files():
        if path.name in ("coordinator.py", "config_flow.py"):
            continue
        for function in functions(parse(path)):
            in_class = isinstance(parents(path).get(function), ast.ClassDef)
            if (in_class and function.name in ACTION_METHODS | registered) or (
                path.name == "services.py" and function.name.startswith("handle_")
            ):
                found.append((path, function))
    return found


def _caught_by_element(
    handler: ast.ExceptHandler, module: types.ModuleType
) -> list[tuple[tuple[type, ...], bool]]:
    """Return each element of *handler*'s type with whether it names a class itself.

    A member that only arrives through a shared tuple (``*COMMAND_EXCEPTIONS``)
    may repeat an earlier clause: the tuple is shared, the clause is not dead.
    """
    elements = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    found = []
    for element in elements:
        starred = isinstance(element, ast.Starred)
        caught = resolve(element.value if starred else element, module) or ()
        named = not starred and len(caught) == 1
        found.append((caught, named))
    return found


def test_except_clauses_are_ordered_child_before_parent() -> None:
    """A handler behind its parent class never runs (§14.3)."""
    shadowed: list[str] = []
    for path in source_files():
        module = integration_module(path)
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.Try | ast.TryStar):
                continue
            earlier: list[type] = []
            for handler in node.handlers:
                elements = _caught_by_element(handler, module)
                behind = {
                    cls: parent
                    for caught, _ in elements
                    for cls in caught
                    if (parent := next((p for p in earlier if issubclass(cls, p)), None))
                }
                every = [cls for caught, _ in elements for cls in caught]
                if every and all(cls in behind for cls in every):
                    shadowed.append(f"{rel(path)}:{handler.lineno} the whole clause")
                shadowed.extend(
                    f"{rel(path)}:{handler.lineno} {caught[0].__name__} behind {behind[caught[0]].__name__}"
                    for caught, named in elements
                    if named and caught[0] in behind
                )
                earlier.extend(every)
    assert not shadowed, shadowed


def _returns_a_value(function: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Return)
        and node.value is not None
        and not (isinstance(node.value, ast.Constant) and node.value.value is None)
        for node in own_nodes(function)
    )


def test_no_catch_all_turns_a_setup_failure_into_a_value() -> None:
    """``except Exception`` that returns lets setup report success on a failure (§14.4, #915)."""
    counts: collections.Counter[str] = collections.Counter()
    for name in SETUP_MODULES:
        path = _path(name)
        module = integration_module(path)
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.ExceptHandler):
                continue
            caught = resolve(node.type, module) or ()
            if not {Exception, BaseException} & set(caught):
                continue
            body = ast.Module(body=node.body, type_ignores=[])
            raises = any(isinstance(n, ast.Raise) for n in own_nodes(body))
            function = _enclosing_function(path, node)
            if function is None or raises:
                continue
            if _returns_a_value(body) or _returns_a_value(function):
                qualname = _qualname(path, function)
                if qualname not in CATCH_ALL_ALLOWED:
                    counts[qualname] += 1
    assert_ratchet(
        dict(counts), CATCH_ALL_RETURNS, "CATCH_ALL_RETURNS", "re-raise, or allow-list it with the reason"
    )


def test_the_catch_all_allow_list_has_no_stale_entries() -> None:
    """An allow-listed function that no longer exists only hides the next one."""
    known = {
        _qualname(_path(name), function)
        for name in SETUP_MODULES
        for function in functions(parse(_path(name)))
    }
    assert set(CATCH_ALL_ALLOWED) <= known, sorted(set(CATCH_ALL_ALLOWED) - known)


def _action_reachable_coordinator_methods() -> set[str]:
    """Return the coordinator methods an entity action reaches, transitively."""
    reached: set[str] = set()
    for _, function in _platform_action_functions():
        reached |= {
            target.rsplit(".", 1)[-1]
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and ".coordinator." in f".{(target := dotted(node.func))}"
        }
    for path in source_files():
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.keyword) and (node.arg or "").endswith("_fn"):
                reached |= {
                    dotted(call.func).rsplit(".", 1)[-1]
                    for call in ast.walk(node.value)
                    if isinstance(call, ast.Call)
                    and dotted(call.func).startswith("coordinator.")
                }
    path = _path("coordinator.py")
    by_name = collections.defaultdict(list)
    for function in functions(parse(path)):
        by_name[function.name].append(function)
    pending = list(reached)
    while pending:
        for function in by_name.get(pending.pop(), []):
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and (target := dotted(node.func)).startswith("self.")
                    and (name := target.removeprefix("self.")) not in reached
                    and name in by_name
                ):
                    reached.add(name)
                    pending.append(name)
    return reached


def test_actions_do_not_swallow_failures() -> None:
    """``except ...: pass`` in an action path ends the action as a success (§14.4)."""
    reachable = _action_reachable_coordinator_methods()
    counts: collections.Counter[str] = collections.Counter()
    scopes = [
        (_path("coordinator.py"), f)
        for f in functions(parse(_path("coordinator.py")))
        if f.name in reachable
    ] + _platform_action_functions()
    for path, function in scopes:
        for node in ast.walk(function):
            if isinstance(node, ast.ExceptHandler) and all(
                isinstance(statement, ast.Pass) for statement in node.body
            ):
                counts[_qualname(path, function)] += 1
    assert_ratchet(dict(counts), SWALLOWED_IN_ACTIONS, "SWALLOWED_IN_ACTIONS", "raise a translated error")


def test_entity_actions_raise_only_home_assistant_errors() -> None:
    """A raw library exception stops a script that ``continue_on_error`` would carry on (§14.5, #914)."""
    unmapped = _unmapped_coordinator_methods()
    counts: collections.Counter[str] = collections.Counter()
    for path, function in _platform_action_functions():
        module = integration_module(path)
        for node in own_nodes(function):
            if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
                continue
            target = dotted(node.value.func)
            via_coordinator = ".coordinator." in f".{target}" and target.rsplit(".", 1)[-1] in unmapped
            if (via_coordinator or _is_raw_library_call(node.value)) and not _guarded(
                path, node, module
            ):
                counts[_qualname(path, function)] += 1
    for path in source_files():
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.keyword) and (node.arg or "").endswith("_fn"):
                for call in ast.walk(node.value):
                    target = dotted(call.func) if isinstance(call, ast.Call) else ""
                    if target.startswith("coordinator.") and target.rsplit(".", 1)[-1] in unmapped:
                        counts[f"{rel(path)}::{node.arg}={target}"] += 1
    assert_ratchet(
        dict(counts),
        UNMAPPED_ACTION_CALLS,
        "UNMAPPED_ACTION_CALLS",
        "route it through _async_device_call, or map COMMAND_EXCEPTIONS to a translated error",
    )


def test_raised_errors_are_translated() -> None:
    """Every ``HomeAssistantError`` an action raises names its translation (§14.5)."""
    counts: collections.Counter[str] = collections.Counter()
    for path in source_files():
        for node in ast.walk(parse(path)):
            if (
                isinstance(node, ast.Raise)
                and isinstance(node.exc, ast.Call)
                and dotted(node.exc.func) in _HA_ERRORS
                and not {"translation_domain", "translation_key"}
                <= {k.arg for k in node.exc.keywords}
            ):
                counts[rel(path)] += 1
    assert_ratchet(
        dict(counts), UNTRANSLATED_RAISES, "UNTRANSLATED_RAISES", "pass translation_domain and translation_key"
    )


def test_no_hand_copied_exception_lists() -> None:
    """A copy of ``COMMAND_EXCEPTIONS`` drifts the day a class is added to it (§14.6)."""
    names = {cls.__name__ for cls in COMMAND_EXCEPTIONS}
    counts: collections.Counter[str] = collections.Counter()
    for path in source_files():
        if path.name == "const.py":
            continue
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Tuple) and (
                sum(dotted(element).rsplit(".", 1)[-1] in names for element in node.elts) >= 3
            ):
                counts[rel(path)] += 1
    assert_ratchet(
        dict(counts), COPIED_EXCEPTION_TUPLES, "COPIED_EXCEPTION_TUPLES", "use COMMAND_EXCEPTIONS"
    )
