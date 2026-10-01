"""Shared scaffolding for the convention tests: paths, parsing, name resolution and the ratchet."""

from __future__ import annotations

import ast
import functools
import importlib
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

TESTS = Path(__file__).resolve().parents[1]
ROOT = TESTS.parent
INTEGRATION = ROOT / "custom_components" / "mammotion"

#: Nodes whose bodies run later, as someone else's callback, not as part of the enclosing function.
DEFERRED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def source_files() -> list[Path]:
    """Return every Python module of the integration."""
    return sorted(INTEGRATION.glob("*.py"))


def rel(path: Path) -> str:
    """Return *path* relative to the repository root."""
    return path.relative_to(ROOT).as_posix()


@functools.cache
def parse(path: Path) -> ast.Module:
    """Return the AST of *path*."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@functools.cache
def parents(path: Path) -> dict[ast.AST, ast.AST]:
    """Map every node of *path*'s AST to its parent."""
    return {
        child: node for node in ast.walk(parse(path)) for child in ast.iter_child_nodes(node)
    }


def integration_module(path: Path) -> ModuleType:
    """Import the integration module *path* is the source of."""
    name = "custom_components.mammotion"
    return importlib.import_module(name if path.stem == "__init__" else f"{name}.{path.stem}")


def own_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Yield the nodes under *node* that run with it, skipping nested functions, lambdas and classes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, DEFERRED):
            continue
        yield child
        yield from own_nodes(child)


def functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield every function and method defined anywhere in *tree*."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def resolve(expr: ast.expr | None, module: ModuleType) -> tuple[type, ...] | None:
    """Return the classes an ``except`` clause catches, or None when a name is local.

    Names are looked up in *module*'s globals, so imports and module-level tuples
    (``COMMAND_EXCEPTIONS``, starred or not) expand to the real classes.
    """
    if expr is None:
        return (BaseException,)
    try:
        value = eval(compile(ast.Expression(expr), "<except>", "eval"), vars(module))  # noqa: S307
    except NameError:
        return None
    flat: list[type] = []
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, tuple):
            pending.extend(item)
        else:
            flat.append(item)
    return tuple(flat)


def dotted(node: ast.AST) -> str:
    """Return ``a.b.c`` for an attribute chain, ``""`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := dotted(node.value)):
        return f"{base}.{node.attr}"
    return ""


def assert_ratchet(actual: dict[str, int], baseline: dict[str, int], rule: str, fix: str) -> None:
    """Fail on a new or worsened violation, and on an improvement left unrecorded."""
    problems: list[str] = []
    for name, count in sorted(actual.items()):
        allowed = baseline.get(name, 0)
        if count > allowed:
            problems.append(f"{name}: {count} (baseline {allowed}) — {fix}")
    for name, allowed in sorted(baseline.items()):
        count = actual.get(name, 0)
        if count < allowed:
            entry = f"set it to {count}" if count else "delete the entry"
            problems.append(f"{name}: down to {count} from {allowed} — {entry} in {rule}")
    assert not problems, f"{rule} (docs/testing.md §14):\n" + "\n".join(f"  {p}" for p in problems)


def assert_frozen(actual: set[str], baseline: frozenset[str], rule: str, fix: str) -> None:
    """Fail on a new member of *actual*, and on a fixed one still in *baseline*."""
    new = sorted(actual - baseline)
    fixed = sorted(baseline - actual)
    assert not new and not fixed, (
        f"{rule} (docs/testing.md §14): new {new} — {fix}; no longer violating, remove from {rule}: {fixed}"
    )
