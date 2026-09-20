"""Spino cleaning modes and the force module are per model, not per enum.

A user reported an E1 offering Waterline and Custom, which that hardware does
not have: the app carries a separate mode enum for the PC210 SP and gates
Waterline on the PC200. The force/turbo toggle is gated the other way — the app
hides it on the S1 and the SP (``SwimmingPoolTestToolsActivity:251-256``).
"""

import ast
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


def test_the_vacuum_fan_speed_list_is_per_device() -> None:
    """It was a module constant, so every cleaner advertised every mode."""
    src = (_ROOT / "vacuum.py").read_text()
    init = _function(src, "__init__")
    assert "SpinoWorkMode.for_device(" in init
    assert "coordinator.device.product_key" in init
    assert "FAN_SPEED_MODES" not in src


def test_the_work_mode_select_is_built_per_device() -> None:
    """Which modes exist is a property of the model, not of the enum."""
    src = (_ROOT / "select.py").read_text()
    setup = _function(src, "async_setup_entry")
    assert "SpinoWorkMode.for_device(" in setup
    # It must no longer sit in the shared static tuple.
    static = src[
        src.index("SPINO_SELECT_ENTITIES") : src.index("async def async_setup_entry")
    ]
    assert "spino_work_mode" not in static


def test_the_work_mode_sensor_also_reports_the_two_non_modes() -> None:
    """The sensor reports rather than commands, so OFF/UNKNOWN belong in it."""
    src = (_ROOT / "sensor.py").read_text()
    setup = _function(src, "async_setup_entry")
    block = setup[setup.index("work_mode_desc") :]
    assert "SpinoWorkMode.UNKNOWN" in block
    assert "SpinoWorkMode.OFF" in block
    assert "SpinoWorkMode.for_device(" in block
    static = src[
        src.index("SPINO_SENSOR_TYPES") : src.index("async def async_setup_entry")
    ]
    assert 'key="spino_work_mode"' not in static


def test_the_turbo_toggle_is_hidden_on_the_s1_and_sp() -> None:
    """The app hides that row for both; every other toggle is unconditional."""
    src = (_ROOT / "switch.py").read_text()
    body = _function(src, "_spino_switch_supported")
    assert 'description.key != "spino_turbo_clean"' in body
    assert "DeviceType.SWIMMINGPOOL_S1" in body
    assert "DeviceType.SWIMMINGPOOL_SP" in body
    setup = _function(src, "async_setup_entry")
    assert "_spino_switch_supported(spino.coordinator, entity_description)" in setup


def test_no_spino_platform_enumerates_the_whole_mode_enum() -> None:
    """The regression: `for mode in SpinoWorkMode` ignored the model entirely."""
    for name in ("vacuum.py", "select.py", "sensor.py"):
        src = (_ROOT / name).read_text()
        assert "for mode in SpinoWorkMode\n" not in src, name
        assert "mode for mode in SpinoWorkMode " not in src, name
