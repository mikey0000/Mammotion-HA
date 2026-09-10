"""Battery charge limit is offered only where the app offers it (issue #857)."""

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


def test_number_adds_charge_limit_only_when_the_device_supports_it() -> None:
    """The slider is not in any ungated tuple and only created behind the gate."""
    src = (_ROOT / "number.py").read_text()
    for tuple_name in (
        "NUMBER_ENTITIES:",
        "AUDIO_NUMBER_ENTITIES:",
        "LUBA_WORKING_ENTITIES:",
        "YUKA_NUMBER_ENTITIES:",
    ):
        start = src.index("\n" + tuple_name)
        assert 'key="charge_limit"' not in src[start : src.index("\n)\n", start)]
    setup = _function(src, "async_setup_entry")
    gate = setup.index("DeviceType.supports_charge_limit(")
    assert gate < setup.index("CHARGE_LIMIT_NUMBER_ENTITY")
    assert "device_firmware_version(mower.reporting_coordinator.data)" in setup[gate:]


def test_charge_limit_slider_matches_the_app() -> None:
    """The app's charge-limit slider runs 80-100 in steps of 5."""
    src = (_ROOT / "number.py").read_text()
    start = src.index("CHARGE_LIMIT_NUMBER_ENTITY =")
    entity = src[start : src.index("\nNUMBER_ENTITIES:", start)]
    assert "native_min_value=80" in entity
    assert "native_max_value=100" in entity
    assert "native_step=5" in entity
    assert "async_set_charge_limit" in entity


def test_switch_adds_smart_charge_only_when_the_device_supports_it() -> None:
    """The smart charging switch is only created behind the same gate."""
    src = (_ROOT / "switch.py").read_text()
    plain = src[src.index("SWITCH_ENTITIES:") : src.index("CHARGE_SWITCH_ENTITIES:")]
    assert 'key="smart_charge"' not in plain
    setup = _function(src, "async_setup_entry")
    gate = setup.index("DeviceType.supports_charge_limit(")
    assert gate < setup.index("CHARGE_SWITCH_ENTITIES")
    assert "device_firmware_version(coordinator.data)" in setup[gate:]


def test_startup_read_of_battery_info_is_gated_the_same_way() -> None:
    """The startup probe is not sent to devices the app would not show the page for."""
    src = (_ROOT / "coordinator.py").read_text()
    idx = src.index('("async_read_battery_info", {})')
    preceding = src[idx - 200 : idx]
    assert "DeviceType.supports_charge_limit(" in preceding


def test_setting_the_limit_resends_the_off_peak_window() -> None:
    """bms_ctrl_info_msg carries every setting, so the off-peak window must be echoed back."""
    src = (_ROOT / "coordinator.py").read_text()
    body = _function(src, "_async_set_battery_info")
    assert '"set_battery_info"' in body
    assert '"bms_ctrl_info_msg"' in body
    for name in (
        "peak_valley_charge",
        "valley_charge_start_time",
        "valley_charge_end_time",
    ):
        assert f"{name}=current.{name}" in body


def test_every_translation_names_the_new_entities() -> None:
    """Every locale and icons.json know the new number and switch keys."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    for path in files:
        data = json.loads(path.read_text())
        assert data["entity"]["number"]["charge_limit"]["name"], path
        assert data["entity"]["switch"]["smart_charge"]["name"], path
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    assert icons["number"]["charge_limit"]["default"].startswith("mdi:")
    assert icons["switch"]["smart_charge"]["default"].startswith("mdi:")
