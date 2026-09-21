"""Map-free mowing ("DropMow"): the app's noAreaWork, as a button.

The mower works from where it stands, with no map and no boundary.  The app
keeps it behind its Beta Features screen and gates it twice — on the X5
platform, and on the mower being idle, refusing otherwise with "Robot is
mowing. Please retry when the robot is idle".  Both gates are mirrored here:
the entity is not created at all off the X5 platform, and it is unavailable
while the mower is busy.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import MowingDevice
from pymammotion.utility.constant import WorkMode

from custom_components.mammotion.button import BUTTON_DROPMOW, BUTTON_SENSORS
from custom_components.mammotion.entity import supports_no_area_work

_X5 = "Luba-VA123456"
_OLDER = "Luba-VS563L6H"


def _description():
    return next(entity for entity in BUTTON_DROPMOW if entity.key == "start_dropmow")


def _coordinator(sys_status: int = WorkMode.MODE_READY) -> MagicMock:
    coordinator = MagicMock()
    device = MowingDevice()
    device.report_data.dev.sys_status = sys_status
    coordinator.data = device
    coordinator.async_start_no_area_work = AsyncMock()
    return coordinator


@pytest.mark.parametrize(
    "device_name",
    [
        "Luba-VA123456",
        "Luba-HM123456",
        "Luba-ME123456",
        "Luba-MB123456",
        "Luba-MD123456",
    ],
)
def test_the_x5_models_are_offered_it(device_name: str) -> None:
    """Mirrors the app's isX5DeviceTyp gate on the DropMow entry point."""
    assert supports_no_area_work(device_name)


@pytest.mark.parametrize("device_name", [_OLDER, "Yuka-MNTXVHBE", "Luba-AAAAAA"])
def test_everything_else_never_gets_the_button(device_name: str) -> None:
    """A mower on the older platform has no map-free mode to start."""
    assert not supports_no_area_work(device_name)


def test_it_is_not_in_the_unconditional_button_set() -> None:
    """Were it there, every mower would get it regardless of platform."""
    assert "start_dropmow" not in {entity.key for entity in BUTTON_SENSORS}


@pytest.mark.parametrize(
    "mode",
    [WorkMode.MODE_READY, WorkMode.MODE_CORRIDOR_DRAW],
    ids=["ready", "corridor-draw"],
)
def test_it_is_available_while_the_mower_is_idle(mode: int) -> None:
    """The two states DropMowHandler accepts the command in."""
    assert _description().available_fn(_coordinator(mode)) is True


@pytest.mark.parametrize(
    "mode",
    [
        WorkMode.MODE_WORKING,
        WorkMode.MODE_PAUSE,
        WorkMode.MODE_RETURNING,
        WorkMode.MODE_CHARGING,
    ],
    ids=["working", "paused", "returning", "charging"],
)
def test_it_is_unavailable_while_the_mower_is_busy(mode: int) -> None:
    """The device refuses it, so offering it would only produce a failed command."""
    assert _description().available_fn(_coordinator(mode)) is False


def test_it_is_unavailable_before_any_state_arrives() -> None:
    """Nothing is known yet, so "idle" is unproven."""
    coordinator = _coordinator()
    coordinator.data = None
    assert _description().available_fn(coordinator) is False


async def test_pressing_it_starts_a_map_free_mow(hass: HomeAssistant) -> None:
    """It must reach the map-free command, not the ordinary start-job one."""
    coordinator = _coordinator()

    await _description().press_fn(coordinator)

    coordinator.async_start_no_area_work.assert_awaited_once()


def test_every_translation_names_the_button() -> None:
    """A key missing from a locale falls back to English mid-interface."""
    root = Path(__file__).parent.parent / "custom_components" / "mammotion"
    files = [root / "strings.json", *sorted((root / "translations").glob("*.json"))]

    for path in files:
        buttons = json.loads(path.read_text())["entity"]["button"]
        assert "start_dropmow" in buttons, path.name
        assert buttons["start_dropmow"]["name"].strip(), path.name

    icons = json.loads((root / "icons.json").read_text())
    assert icons["entity"]["button"]["start_dropmow"]["default"]


def test_only_english_keeps_the_english_wording() -> None:
    """A locale that just copied the English string was never translated.

    "DropMow" is the product name and stays; the verb around it should not.
    """
    root = Path(__file__).parent.parent / "custom_components" / "mammotion"
    english = json.loads((root / "strings.json").read_text())["entity"]["button"][
        "start_dropmow"
    ]["name"]

    copied = [
        path.stem
        for path in sorted((root / "translations").glob("*.json"))
        if path.stem != "en"
        and json.loads(path.read_text())["entity"]["button"]["start_dropmow"]["name"]
        == english
    ]

    assert copied == ["da"], f"unexpectedly untranslated: {copied}"
