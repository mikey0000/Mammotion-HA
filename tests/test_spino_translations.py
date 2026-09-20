"""The Spino pool-cleaner keys stay in sync across every translation file.

The wording is audited against the Mammotion app's own ``strings.xml``; these
tests only guard the mechanical properties that an audit cannot re-check on
every edit: the key set is the same in all thirteen files, and no locale ships
the English string for a key the app really does translate.
"""

import json
from pathlib import Path

_COMPONENT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_SOURCE = _COMPONENT / "strings.json"
_TRANSLATIONS = _COMPONENT / "translations"

# Keys whose English value is also correct in a given locale: loanwords the app
# itself leaves in English ("ECO", "Vinyl"), protocol names and abbreviations.
_SHARED_WITH_ENGLISH = {
    "entity.select.spino_wall_material.state.VINYL": {"cs", "da", "de", "nl", "sv"},
    "entity.select.spino_work_mode.state.ECO": {
        "da",
        "de",
        "fr",
        "hu",
        "it",
        "nl",
        "pl",
        "ro",
        "sv",
    },
    "entity.sensor.spino_work_mode.state.ECO": {
        "da",
        "de",
        "fr",
        "hu",
        "it",
        "nl",
        "pl",
        "ro",
        "sv",
    },
    "entity.sensor.spino_ble_rssi.name": {
        "cs",
        "da",
        "hu",
        "it",
        "nl",
        "pl",
        "ro",
        "sl",
        "sv",
    },
    "entity.sensor.spino_wifi_rssi.name": {"cs", "da", "it", "nl"},
    "entity.sensor.spino_mqtt_status.state.online": {
        "cs",
        "da",
        "de",
        "hu",
        "it",
        "nl",
        "pl",
        "ro",
        "sl",
        "sv",
    },
    "entity.sensor.spino_mqtt_status.state.offline": {
        "cs",
        "da",
        "de",
        "hu",
        "it",
        "nl",
        "pl",
        "ro",
        "sl",
        "sv",
    },
    "entity.sensor.spino_status.name": {"da", "de", "nl", "sv"},
    # The app leaves state_standby untranslated in Italian too.
    "entity.sensor.spino_status.state.IDLE": {"it"},
    "entity.sensor.spino_status.state.PREPARE": {"it"},
    # The app's Romanian title_buzzer is "Buzzerul".
    "entity.switch.spino_buzzer.name": {"ro"},
}


def _flatten(node: object, prefix: str = "") -> dict[str, str]:
    """Return the leaf strings of ``node`` keyed by their dotted path."""
    if not isinstance(node, dict):
        return {prefix: node}
    flat: dict[str, str] = {}
    for key, value in node.items():
        flat.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    return flat


def _spino_strings(path: Path) -> dict[str, str]:
    flat = _flatten(json.loads(path.read_text(encoding="utf-8")))
    return {k: v for k, v in flat.items() if "spino" in k.lower()}


def _locale_files() -> list[Path]:
    return sorted(_TRANSLATIONS.glob("*.json"))


def test_every_locale_has_the_same_spino_keys() -> None:
    """A key added to strings.json must reach all twelve locales."""
    expected = set(_spino_strings(_SOURCE))
    assert expected, "no Spino keys found in strings.json"
    for path in _locale_files():
        assert set(_spino_strings(path)) == expected, path.name


def test_no_locale_falls_back_to_the_english_wording() -> None:
    """An untranslated copy of the English string reaches the user verbatim."""
    english = _spino_strings(_TRANSLATIONS / "en.json")
    for path in _locale_files():
        locale = path.stem
        if locale == "en":
            continue
        for key, value in _spino_strings(path).items():
            if locale in _SHARED_WITH_ENGLISH.get(key, frozenset()):
                continue
            assert value != english[key], f"{locale}: {key} is still English"


def test_english_translation_matches_the_source_strings() -> None:
    """strings.json is the source; en.json must not drift away from it."""
    assert _spino_strings(_TRANSLATIONS / "en.json") == _spino_strings(_SOURCE)
