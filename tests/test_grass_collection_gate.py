"""Sweep, dump and dump-point controls are offered where the app offers them."""

import ast
import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_SERVICE_KEYS = (
    "start_dump_point_setup",
    "add_dump_point",
    "undo_dump_point",
    "finish_dump_point_setup",
    "finish_outside_dump_point",
)
_SWITCH_KEYS = ("manual_grass_collection", "manual_grass_dump")


def _function(src: str, name: str) -> str:
    tree = ast.parse(src)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )
    return ast.get_source_segment(src, node)


def test_capability_helper_matches_the_app() -> None:
    """isSupportGrassCutting() is the original Yuka and the Yuka VP, nothing else."""
    body = _function((_ROOT / "entity.py").read_text(), "supports_grass_collection")
    assert "is_yu_ka()" in body
    assert "is_yu_ka_pro()" in body


def test_switch_adds_sweep_and_dump_only_behind_the_gate() -> None:
    """The manual toggles are not in any ungated tuple."""
    src = (_ROOT / "switch.py").read_text()
    gated = src.index("GRASS_COLLECTION_SWITCH_ENTITIES: tuple[")
    for key in _SWITCH_KEYS:
        assert src.index(f'key="{key}"') > gated
    factory = _function(src, "_grass_collection_entities")
    assert "supports_grass_collection(device_name)" in factory
    assert "GRASS_COLLECTION_SWITCH_ENTITIES" in factory
    setup = _function(src, "async_setup_entry")
    assert "_grass_collection_entities(coordinator, device_name)" in setup
    assert "GRASS_COLLECTION_SWITCH_ENTITIES" not in setup


def test_manual_toggles_are_unavailable_without_a_collector() -> None:
    """The app hides both controls on collector_installation_status == 0."""
    src = (_ROOT / "switch.py").read_text()
    start = src.index("GRASS_COLLECTION_SWITCH_ENTITIES: tuple[")
    block = src[start : src.index("\n)\n", start)]
    assert (
        block.count(
            "available_fn=lambda coordinator: coordinator.grass_collector_installed"
        )
        == 2
    )


def test_dump_points_are_lawn_mower_services_not_buttons() -> None:
    """Each point is taken at the current position, so these are sequenced actions."""
    src = (_ROOT / "lawn_mower.py").read_text()
    for key in _SERVICE_KEYS:
        assert f'"{key}"' in src
    assert "DUMP_POINT_BUTTONS" not in (_ROOT / "button.py").read_text()

    services = yaml.safe_load((_ROOT / "services.yaml").read_text())
    for key in _SERVICE_KEYS:
        assert services[key]["target"]["entity"]["domain"] == "lawn_mower"


def test_dump_point_services_reject_mowers_without_a_collector() -> None:
    """Every handler runs the capability check before touching the coordinator."""
    src = (_ROOT / "lawn_mower.py").read_text()
    assert "supports_grass_collection" in _function(src, "_assert_grass_collection")
    for method in (
        "async_start_dump_point_setup",
        "async_add_dump_point",
        "async_undo_dump_point",
        "async_finish_dump_point_setup",
        "async_finish_outside_dump_point",
    ):
        assert "self._assert_grass_collection()" in _function(src, method)


def test_coordinator_sends_the_commands_the_app_sends() -> None:
    """Each control maps onto the pymammotion command the APK's helper builds."""
    src = (_ROOT / "coordinator.py").read_text()
    for method, command in (
        ("async_set_grass_collection", "manual_grass_collection"),
        ("async_set_grass_dump", "manual_pour_grass"),
        ("async_enter_dump_point_setup", "enter_dumping_status"),
        ("async_add_dump_point", "add_dump_point"),
        ("async_revoke_dump_point", "revoke_dump_point"),
        ("async_exit_dump_point_setup", "exit_dumping_status"),
        ("async_finish_outside_dump_point", "out_drop_dumping_add"),
    ):
        body = _function(src, method)
        assert f'"{command}"' in body
        assert "Priority.USER" in body


def test_state_comes_from_pymammotion_not_a_local_bit_decode() -> None:
    """The sensor_status split lives in the library, next to its sibling accessors."""
    src = (_ROOT / "coordinator.py").read_text()
    assert "dev.collector_state" in _function(src, "grass_collection_state")
    assert "dev.dump_state" in _function(src, "grass_dump_state")
    assert "dev.collector_installed" in _function(src, "grass_collector_installed")
    assert "sensor_status" not in src


def test_every_translation_names_the_new_entities_and_services() -> None:
    """Every locale, icons.json and the exception key are in sync."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    for path in files:
        data = json.loads(path.read_text())
        for key in _SWITCH_KEYS:
            assert data["entity"]["switch"][key]["name"], (path, key)
        for key in _SERVICE_KEYS:
            assert data["services"][key]["name"], (path, key)
            assert data["services"][key]["description"], (path, key)
        assert data["exceptions"]["grass_collection_unsupported"]["message"], path
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    for key in _SWITCH_KEYS:
        assert icons["switch"][key]["default"].startswith("mdi:")


def test_translations_are_not_english_placeholders() -> None:
    """Non-English locales translate the names rather than copying English."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    for path in sorted((_ROOT / "translations").glob("*.json")):
        if path.stem == "en":
            continue
        data = json.loads(path.read_text())
        for key in _SWITCH_KEYS:
            assert (
                data["entity"]["switch"][key]["name"]
                != english["entity"]["switch"][key]["name"]
            ), (path, key)
        for key in _SERVICE_KEYS:
            assert (
                data["services"][key]["description"]
                != english["services"][key]["description"]
            ), (path, key)
