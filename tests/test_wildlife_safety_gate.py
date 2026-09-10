"""Wildlife Safety is offered only where the app offers it (issue #853)."""

import ast
from pathlib import Path

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"


def _function(src: str, name: str) -> str:
    tree = ast.parse(src)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name)
    return ast.get_source_segment(src, node)


def test_select_adds_wildlife_safety_only_when_the_device_supports_it() -> None:
    src = (_ROOT / "select.py").read_text()
    async_tuple = src[src.index("ASYNC_SELECT_ENTITIES:") : src.index("WILDLIFE_SAFETY_SELECT_ENTITY =")]
    assert 'key="wildlife_safety"' not in async_tuple
    setup = _function(src, "async_setup_entry")
    gate = setup.index("DeviceType.supports_wildlife_safety(")
    assert gate < setup.index("WILDLIFE_SAFETY_SELECT_ENTITY")
    assert "_device_firmware_version(mower.reporting_coordinator.data)" in setup[gate:]


def test_startup_read_of_wildlife_safety_is_gated_the_same_way() -> None:
    src = (_ROOT / "coordinator.py").read_text()
    idx = src.index('("async_read_wildlife_safety", {})')
    preceding = src[idx - 400 : idx]
    assert "DeviceType.supports_wildlife_safety(" in preceding
