"""Enabling scheduled updates swallows a transport miss instead of raising into the event bus."""

import ast
from pathlib import Path

_src = (Path(__file__).parent.parent / "custom_components" / "mammotion" / "coordinator.py").read_text()


def test_keep_alive_restart_catches_transport_error() -> None:
    tree = ast.parse(_src)
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "set_scheduled_updates"
    )
    handlers = [
        h
        for node in ast.walk(fn)
        if isinstance(node, ast.Try)
        for h in node.handlers
        if isinstance(h.type, ast.Name) and h.type.id == "TransportError"
    ]
    assert len(handlers) == 1
    body = ast.get_source_segment(_src, fn)
    assert body.index("await handle.restart_keep_alive()") > body.index("try:")
