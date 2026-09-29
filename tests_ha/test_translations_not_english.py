"""No non-English locale ships a string copied verbatim from English.

A copy renders as English to a user who picked another language, and nothing
else catches it: the key is present, so HA never falls back or warns.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_TRANSLATIONS = _ROOT / "translations"

#: Identical in every language: placeholders, product names, technology names.
_NEUTRAL = frozenset(
    {
        "{name}",
        "{device_name}: {state}",
        "iNavi NetRTK",
        "iNavi RTK Box",
        "Bluetooth",
        "BLE",
        "BLE RSSI",
        "Wi-Fi",
        "Wi-Fi RSSI",
        "WiFi RSSI",
        "YYYY-MM-DD.",
        "OK",
        "Online",
        "Offline",
        "Cloud",
        "Eco",
        "Standard",
    }
)

#: Words spelled the same as English that are also the native word in these locales.
_COGNATES: dict[str, frozenset[str]] = {
    "Name": frozenset({"de"}),
    "Status": frozenset({"da", "de", "nl", "sv"}),
    "Information": frozenset({"da", "de", "fr", "sv"}),
    "Vinyl": frozenset({"cs", "da", "de", "nl", "sv"}),
    "Rotation": frozenset({"da", "fr", "sv"}),
    "Direct": frozenset({"fr", "nl", "ro"}),
    "Optimal": frozenset({"da", "sv"}),
    "Start DropMow": frozenset({"da"}),
    "Latitude": frozenset({"fr"}),
    "Longitude": frozenset({"fr"}),
    "Satellites": frozenset({"fr"}),
    "Notification": frozenset({"fr"}),
    "Notifications": frozenset({"fr"}),
    "cycles": frozenset({"fr"}),
}


def _flatten(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[str, str]]:
    """Yield ``(dotted.path, text)`` for every string leaf."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, (*path, key))
    elif isinstance(node, str):
        yield ".".join(path), node


def _load(path: Path) -> dict[str, str]:
    """Return one translation file as ``{dotted.path: text}``."""
    return dict(_flatten(json.loads(path.read_text(encoding="utf-8"))))


def _identical_to_english() -> dict[str, dict[str, str]]:
    """Map each locale to its ``{path: text}`` entries equal to English at that path.

    Both ``strings.json`` and ``en.json`` count as English: they have drifted,
    and a locale copied from either one is still English.
    """
    english = [_load(_ROOT / "strings.json"), _load(_TRANSLATIONS / "en.json")]
    return {
        path.stem: {
            key: text
            for key, text in _load(path).items()
            if any(source.get(key) == text for source in english)
        }
        for path in sorted(_TRANSLATIONS.glob("*.json"))
        if path.stem != "en"
    }


def _allowed(locale: str, text: str) -> bool:
    """Return True when ``text`` may legitimately match English in ``locale``."""
    return text in _NEUTRAL or locale in _COGNATES.get(text, frozenset())


def test_no_locale_carries_an_english_copy() -> None:
    """Every string outside the allowlist is written in the locale's own language."""
    offenders = {
        locale: [f"{key}: {text!r}" for key, text in found.items()]
        for locale, identical in _identical_to_english().items()
        if (found := {k: t for k, t in identical.items() if not _allowed(locale, t)})
    }

    assert not offenders, json.dumps(offenders, indent=1, ensure_ascii=False)


def test_the_allowlist_has_no_stale_entries() -> None:
    """An entry nothing needs any more only hides the next real copy."""
    used = {
        (locale, text)
        for locale, identical in _identical_to_english().items()
        for text in identical.values()
    }

    unused_neutral = _NEUTRAL - {text for _, text in used}
    assert not unused_neutral, sorted(unused_neutral)
    stale = {
        (locale, text)
        for text, locales in _COGNATES.items()
        for locale in locales
        if (locale, text) not in used
    }
    assert not stale, sorted(stale)


def test_every_locale_has_the_same_keys_as_strings_json() -> None:
    """A missing key falls back to English silently; an extra one is dead."""
    expected = set(_load(_ROOT / "strings.json"))
    for path in sorted(_TRANSLATIONS.glob("*.json")):
        assert set(_load(path)) == expected, path.stem
