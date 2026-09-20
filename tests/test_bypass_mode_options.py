"""Obstacle-detection options follow the app's value and label scheme (issue #887).

The app's off position is value 1 on the new Off/Standard/Sensitive list and
value 0 on the older touch lists, and it labels a value by which list the
device has. The select therefore builds its options from label keys rather
than from the protocol member names.
"""

import ast
import json
from pathlib import Path

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_OPTION_KEYS = ("off", "slow_touch", "less_touch", "standard", "sensitive")


def _function(src: str, name: str) -> str:
    tree = ast.parse(src)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )
    return ast.get_source_segment(src, node)


def test_options_are_label_keys_not_member_names() -> None:
    """``s.name`` would show "Slow touch" for the new list's off position."""
    src = (_ROOT / "select.py").read_text()
    setup = _function(src, "async_setup_entry")
    block = setup[setup.index("bypass_options = DetectionStrategy.for_device") :]
    assert "s.option_key(bypass_options) for s in bypass_options" in block
    assert "DetectionStrategy.from_option_key(" in _function(src, "_set_bypass_mode")
    assert "DetectionStrategy[value].value" not in src


def test_the_device_list_is_bound_per_description() -> None:
    """The description outlives the loop; a closure would read the last mower's list."""
    setup = _function((_ROOT / "select.py").read_text(), "async_setup_entry")
    assert "partial(_set_bypass_mode, bypass_options)" in setup


def test_every_translation_uses_the_new_option_keys() -> None:
    """direct_touch/no_touch are gone; the keys follow the app's own strings."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    for path in files:
        state = json.loads(path.read_text())["entity"]["select"]["bypass_mode"]["state"]
        assert tuple(state) == _OPTION_KEYS, path
        assert all(state.values()), path


def test_the_service_selector_matches_the_entity_wording() -> None:
    """start_mow labels raw values, but must not disagree with the entity."""
    for path in [
        _ROOT / "strings.json",
        *sorted((_ROOT / "translations").glob("*.json")),
    ]:
        data = json.loads(path.read_text())
        options = data["selector"]["ultra_wave"]["options"]
        assert tuple(options) == ("0", "1", "2", "10", "11"), path
        state = data["entity"]["select"]["bypass_mode"]["state"]
        assert options["0"] == state["off"], path
        assert options["1"] == state["slow_touch"], path
        assert options["2"] == state["less_touch"], path
        assert options["10"] == state["standard"], path
        assert options["11"] == state["sensitive"], path


def test_only_english_keeps_the_english_wording() -> None:
    """The APK's own per-locale strings, not English placeholders."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    english_state = english["entity"]["select"]["bypass_mode"]["state"]
    for path in sorted((_ROOT / "translations").glob("*.json")):
        if path.stem in ("en", "da", "sv"):
            # da/sv legitimately share "Standard" with English.
            continue
        state = json.loads(path.read_text())["entity"]["select"]["bypass_mode"]["state"]
        assert state["off"] != english_state["off"], path
        assert state["slow_touch"] != english_state["slow_touch"], path
