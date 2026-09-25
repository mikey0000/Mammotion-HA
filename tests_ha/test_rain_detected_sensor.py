"""The ``rain_detected`` binary sensor reads the report's self-check code.

The app shows "Mowing is disabled on rainy days" when
``rpt_dev_status.self_check_status`` is 20 (``BlockErrorBeanChangeUtils.ERROR_CODE_13``).
"""

import json
from pathlib import Path

import pytest
from pymammotion.data.model.device import MowingDevice

from custom_components.mammotion.binary_sensor import BINARY_SENSORS

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_RAIN = next(d for d in BINARY_SENSORS if d.key == "rain_detected")


@pytest.mark.parametrize(
    ("self_check_status", "expected"),
    [(20, True), (0, False), (10, False), (21, False)],
)
def test_rain_detected_follows_the_self_check_code(
    self_check_status: int, expected: bool
) -> None:
    """Only code 20 means rain; the field is one code, not a bitmask."""
    device = MowingDevice()
    device.report_data.dev.self_check_status = self_check_status

    assert _RAIN.is_on_fn(device) is expected


@pytest.mark.parametrize(
    "path",
    [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))],
    ids=lambda path: path.name,
)
def test_every_locale_names_the_sensor(path: Path) -> None:
    """The entity needs a translated name in every locale."""
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["entity"]["binary_sensor"]["rain_detected"]["name"]


def test_the_sensor_has_an_icon() -> None:
    """A rainy icon while on, a sunny one while off."""
    icons = json.loads((_ROOT / "icons.json").read_text())
    assert icons["entity"]["binary_sensor"]["rain_detected"]["state"]["on"]
