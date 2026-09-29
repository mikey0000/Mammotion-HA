"""Every remote-drive string exists in every locale, with the placeholders English has.

The exception keys are read off the source that raises or shows them, so a key added
there without a translation fails here.
"""

import json
import re
from pathlib import Path

import pytest
from pymammotion.device.remote_drive import RemoteDriveEventKind, RemoteDrivePhase

from custom_components.mammotion.notifications import QUIET_REMOTE_DRIVE_KINDS

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_ENTITY_KEYS = {
    ("sensor", "remote_drive_state"),
    ("switch", "remote_drive"),
    ("button", "confirm_remote_drive"),
    ("button", "acknowledge_remote_drive_fence"),
}


def _exception_keys() -> set[str]:
    """Return the remote-drive keys the coordinator raises and the notifier shows."""
    coordinator = (_ROOT / "coordinator.py").read_text()
    notifier = (_ROOT / "notifications.py").read_text()
    literal = re.compile(r'"((?:remote_drive|movement|notification_title_remote)\w*)"')
    raised = set(
        re.findall(r'translation_key="((?:remote_drive|movement)\w*)"', coordinator)
    )
    shown = {
        key
        for key in literal.findall(notifier)
        if key.startswith(("remote_drive_safety_notice", "notification_title"))
    }
    # The notifier and the start refusal build these from the event kind.
    by_kind = {
        f"remote_drive_{kind.value}"
        for kind in RemoteDriveEventKind
        if kind not in QUIET_REMOTE_DRIVE_KINDS
    }
    return raised | shown | by_kind


def test_the_key_scan_finds_what_it_should() -> None:
    """Guard the regexes: a scan that finds nothing would pass every locale."""
    keys = _exception_keys()

    assert {
        "movement_unavailable",
        "remote_drive_needs_cloud",
        "remote_drive_safety_notice",
        "notification_title_remote_drive",
        "remote_drive_approach_fence",
    } <= keys


def _locale_files() -> list[Path]:
    return [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]


def _placeholders(text: str) -> set[str]:
    return {part.split("}")[0] for part in text.split("{")[1:]}


@pytest.mark.parametrize("path", _locale_files(), ids=lambda path: path.name)
def test_every_locale_has_the_remote_drive_strings(path: Path) -> None:
    """Every name, phase and message, with the placeholders the English one has."""
    strings = json.loads(path.read_text())
    english = json.loads((_ROOT / "strings.json").read_text())
    for platform, key in _ENTITY_KEYS:
        assert strings["entity"][platform][key]["name"], (platform, key)
    assert set(strings["entity"]["sensor"]["remote_drive_state"]["state"]) == {
        phase.value for phase in RemoteDrivePhase
    }
    for key in _exception_keys():
        message = strings["exceptions"][key]["message"]
        expected = english["exceptions"][key]["message"]
        assert _placeholders(message) == _placeholders(expected), (path.name, key)


def test_the_new_entities_have_icons() -> None:
    """Every remote-drive entity has an icon, and the sensor one for every phase."""
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    for platform, key in _ENTITY_KEYS:
        assert icons[platform][key]["default"], (platform, key)
    assert set(icons["sensor"]["remote_drive_state"]["state"]) == {
        phase.value for phase in RemoteDrivePhase
    }
