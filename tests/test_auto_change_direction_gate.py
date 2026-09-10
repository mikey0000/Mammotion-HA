"""Auto-reverse mowing direction is offered only where the app offers it (issue #860)."""

import ast
import json
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


def test_switch_adds_the_toggle_only_when_the_device_supports_it() -> None:
    """The switch is absent from the ungated config tuple and created behind the gate."""
    src = (_ROOT / "switch.py").read_text()
    ungated = src[
        src.index("CONFIG_SWITCH_ENTITIES:") : src.index(
            "AUTO_CHANGE_DIRECTION_CONFIG_SWITCH_ENTITIES:"
        )
    ]
    assert 'key="auto_change_direction"' not in ungated
    setup = _function(src, "async_setup_entry")
    gate = setup.index("DeviceType.supports_auto_change_direction(")
    assert gate < setup.index("AUTO_CHANGE_DIRECTION_CONFIG_SWITCH_ENTITIES")
    assert "device_firmware_version(coordinator.data)" in setup[gate:]


def test_the_switch_writes_the_operation_setting() -> None:
    """Toggling stores 0/1 on operation_settings so the next plan carries it."""
    src = (_ROOT / "switch.py").read_text()
    start = src.index("AUTO_CHANGE_DIRECTION_CONFIG_SWITCH_ENTITIES:")
    entity = src[start : src.index("\n)\n", start)]
    assert 'key="auto_change_direction"' in entity
    assert (
        "setattr(\n            coordinator.operation_settings,"
        ' "auto_change_direction", int(value)'
    ) in entity


def test_route_generation_carries_the_setting() -> None:
    """generate_route_information passes the toggle through to pymammotion."""
    src = (_ROOT / "coordinator.py").read_text()
    body = _function(src, "generate_route_information")
    assert "auto_change_direction=operation_settings.auto_change_direction" in body


def test_every_translation_names_the_new_switch() -> None:
    """Every locale and icons.json know the new switch key, each in its own language."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    names = {}
    for path in files:
        data = json.loads(path.read_text())
        name = data["entity"]["switch"]["auto_change_direction"]["name"]
        assert name, path
        names[path.name] = name
    # Only strings.json and en.json may share the English wording.
    english = names["strings.json"]
    assert [f for f, n in names.items() if n == english] == ["strings.json", "en.json"]
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    assert icons["switch"]["auto_change_direction"]["default"].startswith("mdi:")
