"""Every locale names both rain-protection selects and every option they offer.

The options are read off the entity descriptions, so an option added there without
a translation fails here, and the icons cover the same keys.
"""

import json
from pathlib import Path

import pytest

from custom_components.mammotion.select import (
    RAIN_PROTECTION_SELECT_ENTITIES,
    MammotionRainProtectionSelectEntityDescription,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"


def _locale_files() -> list[Path]:
    return [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]


def test_the_descriptions_offer_what_the_app_does() -> None:
    """Guard the parametrisation: an empty option list would pass every locale."""
    options = {d.key: d.options for d in RAIN_PROTECTION_SELECT_ENTITIES}

    assert options == {
        "rain_protection_mode": ["off", "smart", "sensor"],
        "rain_protection_delay": [str(h) for h in (*range(13), 24, 48)],
    }


@pytest.mark.parametrize("path", _locale_files(), ids=lambda path: path.name)
@pytest.mark.parametrize(
    "description", RAIN_PROTECTION_SELECT_ENTITIES, ids=lambda d: d.key
)
def test_every_locale_translates_the_name_and_every_option(
    path: Path, description: MammotionRainProtectionSelectEntityDescription
) -> None:
    """A missing option would show the raw value (``24``) in that language."""
    select = json.loads(path.read_text(encoding="utf-8"))["entity"]["select"]
    entry = select[description.key]

    assert entry["name"]
    assert set(entry["state"]) == set(description.options)
    assert all(entry["state"].values()), entry["state"]


@pytest.mark.parametrize(
    "description", RAIN_PROTECTION_SELECT_ENTITIES, ids=lambda d: d.key
)
def test_each_select_has_an_icon(
    description: MammotionRainProtectionSelectEntityDescription,
) -> None:
    """Both selects carry an icon entry."""
    icons = json.loads((_ROOT / "icons.json").read_text(encoding="utf-8"))

    assert icons["entity"]["select"][description.key]["default"].startswith("mdi:")
