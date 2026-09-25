"""The ``self_check`` enum sensor names why the mower will not start.

Codes and their meanings come from the app's ``BlockErrorBeanChangeUtils``.
"""

import json
from pathlib import Path

import pytest
from pymammotion.data.model.device import MowingDevice

from custom_components.mammotion.const import SELF_CHECK_OTHER, SELF_CHECK_STATES
from custom_components.mammotion.sensor import SENSOR_TYPES

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_SELF_CHECK = next(d for d in SENSOR_TYPES if d.key == "self_check")


@pytest.mark.parametrize(
    ("code", "state"),
    [
        (0, "normal"),
        (10, "normal"),
        (11, "emergency_stop"),
        (20, "rain"),
        (23, "non_working_hours"),
        (99, "weak_rtk_signal"),
        (55, SELF_CHECK_OTHER),
    ],
)
def test_codes_map_to_their_state(code: int, state: str) -> None:
    """Both idle codes read as normal; a code the app has no card for is 'other'."""
    device = MowingDevice()
    device.report_data.dev.self_check_status = code

    assert _SELF_CHECK.value_fn(device) == state


def test_every_state_is_an_option() -> None:
    """HA rejects an enum value missing from ``options``."""
    assert _SELF_CHECK.options is not None
    assert {*SELF_CHECK_STATES.values(), SELF_CHECK_OTHER} == set(_SELF_CHECK.options)


@pytest.mark.parametrize(
    "path",
    [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))],
    ids=lambda path: path.name,
)
def test_every_locale_translates_every_state(path: Path) -> None:
    """Each locale names the sensor and every one of its states."""
    entry = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"][
        "self_check"
    ]
    assert entry["name"]
    assert _SELF_CHECK.options is not None
    missing = [s for s in _SELF_CHECK.options if not entry["state"].get(s)]
    assert missing == []
