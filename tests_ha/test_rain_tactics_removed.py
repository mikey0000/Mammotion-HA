"""The plan-level ``rain_tactics`` setting is gone from every HA surface.

No app sends a rain value in a route or plan; byte 2 of the path order is the
plan enable flag. The only rain setting is the device-level ``rain_detection``
switch, which stays.  The job services still accept and ignore the field
(``test_lawn_mower_retired_job_field.py``); its repair text is checked here.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from custom_components.mammotion import switch as switch_platform

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_LOCALES = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for value in node.values() for k in _keys(value)}
    if isinstance(node, list):
        return {k for value in node for k in _keys(value)}
    return set()


def _switch_keys() -> set[str]:
    return {
        description.key
        for value in vars(switch_platform).values()
        if isinstance(value, tuple)
        for description in value
        if hasattr(description, "key") and hasattr(description, "set_fn")
    }


@pytest.mark.regression
def test_services_yaml_does_not_offer_rain_tactics() -> None:
    """services.yaml advertised the dead field on the job services."""
    assert "rain_tactics" not in _keys(
        yaml.safe_load((_ROOT / "services.yaml").read_text())
    )


@pytest.mark.regression
def test_the_rain_tactics_switch_is_gone_and_rain_detection_stays() -> None:
    """The config switch toggled a setting no route carried; the device-level switch is the real one."""
    keys = _switch_keys()

    assert "rain_tactics" not in keys
    assert "rain_detection" in keys


@pytest.mark.regression
@pytest.mark.parametrize(
    "path", [*_LOCALES, _ROOT / "icons.json"], ids=lambda p: p.name
)
def test_no_string_or_icon_entry_is_left_for_rain_tactics(path: Path) -> None:
    """Entity, service-field and selector translations, and the switch icon, all went with it."""
    assert "rain_tactics" not in _keys(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", _LOCALES, ids=lambda p: p.name)
def test_the_retired_field_repair_is_translated_with_its_placeholders(
    path: Path,
) -> None:
    """A missing key or placeholder would show the user a blank repair."""
    issue = json.loads(path.read_text(encoding="utf-8"))["issues"][
        "deprecated_rain_tactics"
    ]

    assert issue["title"]
    assert "{service}" in issue["description"]
    assert "{field}" in issue["description"]
