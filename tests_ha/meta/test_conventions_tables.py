"""Tables the integration must keep in step with pymammotion and its JSON (docs/testing.md §14.7–§14.9).

Diagnostics redaction against the library's models, mode tables taken from the
library by identity, and strings.json against the code, the locales and
icons.json.  Baselines are ratchets, as in ``test_conventions.py``.
"""

from __future__ import annotations

import ast
import collections
import dataclasses
import json
import re
import typing

from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.data.model.device import (
    MowingDevice,
    PoolCleanerDevice,
    RTKBaseStationDevice,
)
from pymammotion.data.mqtt import mammotion_properties
from pymammotion.utility import constant as library_constants
from pymammotion.utility.constant import WorkMode

from custom_components.mammotion import diagnostics

from .conventions_support import (
    INTEGRATION,
    assert_frozen,
    assert_ratchet,
    dotted,
    integration_module,
    parse,
    rel,
    source_files,
)

#: Identifier-shaped model fields deliberately left in diagnostics, and why (§14.7).
REDACTION_ALLOWED: dict[str, str] = {
    "iot_id": "what makes a multi-device dump readable (diagnostics.py)",
    "identity_id": "what makes a multi-device dump readable (diagnostics.py)",
    "product_key": "names the model, the same for every unit of it",
    "category_key": "names the product category",
    "_category_key": "names the product category",
    "is_edge_gateway": "a flag, not an address",
}

#: Identifier-shaped model fields not yet in ``TO_REDACT`` nor decided on (§14.7).
UNREDACTED_FIELDS: frozenset[str] = frozenset()

#: Literal collections of ``WorkMode`` members, per module (§14.8).
WORKMODE_LITERALS: dict[str, int] = {
    "custom_components/mammotion/button.py": 1,
    "custom_components/mammotion/coordinator.py": 2,
    "custom_components/mammotion/lawn_mower.py": 8,
}

#: Module-level WorkMode tables the library has no counterpart for, and why.
MODE_TABLES_ALLOWED: dict[str, str] = {
    "custom_components/mammotion/button.py::_DROPMOW_MODES": "the app's DropMowHandler gate",
    "custom_components/mammotion/coordinator.py::ERROR_LOG_READ_MODES": "when this integration reads the error log",
}

#: Exception translation keys used in code but missing from strings.json.
MISSING_EXCEPTION_KEYS: frozenset[str] = frozenset()

#: ``platform.translation_key`` of entities with no strings.json entry.
MISSING_ENTITY_TRANSLATIONS: frozenset[str] = frozenset(
    {"device_tracker.device_tracker", "update.update"}
)

#: ``platform.translation_key`` of entities with neither an ``icon`` nor an icons.json entry.
MISSING_ICONS: frozenset[str] = frozenset(
    {
        "button.refresh_status",
        "button.relocate_charging_station",
        "button.restart_mower",
        "button.spino_refresh_status",
        "button.start_task_sync",
        "camera.webrtc_camera",
        "camera.webrtc_camera_rear",
        "camera.webrtc_camera_right",
        "camera.webrtc_camera_vision",
        "number.voice_volume",
        "select.cutter_mode",
        "select.spino_bottom_type",
        "select.spino_wall_material",
        "select.spino_work_mode",
        "select.turning_mode",
        "select.voice_gender",
        "select.wildlife_safety",
        "sensor.non_work_hours",
        "sensor.rtk_latitude",
        "sensor.rtk_longitude",
        "sensor.rtk_lora",
        "sensor.rtk_sats_num",
        "switch.bluetooth_enabled",
        "switch.cloud_enabled",
        "switch.manual_light",
        "switch.night_light",
        "switch.rain_detection",
        "switch.spino_buzzer",
        "switch.spino_platform_cleaning",
        "switch.spino_turbo_clean",
        "switch.spino_waterline_parking",
        "switch.voice_on_off",
    }
)

_IDENTIFIER_FIELD = re.compile(
    r"imei|imsi|iccid|mac|ssid|(^|_)ip($|_)|ip_address|token|key|secret|password|serial|sn$|gateway|mask"
)
_STRINGS = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
_ICONS = json.loads((INTEGRATION / "icons.json").read_text(encoding="utf-8"))


def _model_classes(cls: type, seen: set[type]) -> None:
    """Collect *cls* and every dataclass reachable through its field types."""
    if cls in seen or not dataclasses.is_dataclass(cls):
        return
    seen.add(cls)
    try:
        hints = typing.get_type_hints(cls)
    except (NameError, TypeError):
        hints = {field.name: field.type for field in dataclasses.fields(cls)}
    pending = list(hints.values())
    while pending:
        hint = pending.pop()
        if isinstance(hint, type):
            _model_classes(hint, seen)
        pending.extend(typing.get_args(hint))


#: Models that reach the dump only as JSON strings (``mqtt_properties``' ``networkInfo``
#: and kin), so no field type leads to them (#921).
_JSON_STRING_MODELS = tuple(
    value for value in vars(mammotion_properties).values() if dataclasses.is_dataclass(value)
)


def _identifier_fields() -> set[str]:
    assert mammotion_properties.NetworkInfo in _JSON_STRING_MODELS
    classes: set[type] = set()
    for root in (MowingDevice, RTKBaseStationDevice, PoolCleanerDevice, Device, *_JSON_STRING_MODELS):
        _model_classes(root, classes)
    return {
        field.name
        for cls in classes
        for field in dataclasses.fields(cls)
        if _IDENTIFIER_FIELD.search(field.name.lower())
    }


def test_diagnostics_redact_every_identifier_field() -> None:
    """Diagnostics are pasted into public issues; a new identifier field needs a decision (§14.7, #919)."""
    undecided = _identifier_fields() - set(diagnostics.TO_REDACT) - set(REDACTION_ALLOWED)
    assert_frozen(
        undecided, UNREDACTED_FIELDS, "UNREDACTED_FIELDS", "add it to TO_REDACT or to REDACTION_ALLOWED"
    )


def _is_mode_collection(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and dotted(node.func) in ("frozenset", "set", "tuple"):
        node = node.args[0] if node.args else node
    return (
        isinstance(node, ast.Tuple | ast.Set | ast.List)
        and len(node.elts) >= 2
        and all(dotted(element).startswith("WorkMode.") for element in node.elts)
    )


def test_mode_tables_come_from_pymammotion() -> None:
    """A copied mode table misses the modes the library adds later (§14.8, #917)."""
    counts: collections.Counter[str] = collections.Counter()
    for path in source_files():
        counts.update(rel(path) for node in ast.walk(parse(path)) if _is_mode_collection(node))
    assert "custom_components/mammotion/const.py" not in counts, "const.py defines a WorkMode table"
    assert_ratchet(
        dict(counts), WORKMODE_LITERALS, "WORKMODE_LITERALS", "use the pymammotion table (utility.constant)"
    )


def test_module_level_mode_tables_are_the_librarys_objects() -> None:
    """A module-level WorkMode collection must be the library's own object, by identity."""
    library = [vars(library_constants)[name] for name in dir(library_constants)]
    copies = [
        f"{rel(path)}::{name}"
        for path in source_files()
        for name, value in vars(integration_module(path)).items()
        if isinstance(value, tuple | set | frozenset)
        and len(value) >= 2
        and all(isinstance(member, WorkMode) for member in value)
        and not any(value is table for table in library)
        and f"{rel(path)}::{name}" not in MODE_TABLES_ALLOWED
    ]
    assert not copies, copies


def test_en_json_is_strings_json() -> None:
    """Tests and Home Assistant load en.json; it is a copy of strings.json (§14.9)."""
    english = json.loads((INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8"))
    assert english == _STRINGS


def _exception_keys_used() -> set[str]:
    """Return the literal translation keys raises, failure keys and issues use."""
    keys: set[str] = set()
    for path in source_files():
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Call):
                func = dotted(node.func)
                if func.endswith(("EntityDescription", "SelectorConfig", "async_create_issue")):
                    continue
                keys |= {
                    k.value.value
                    for k in node.keywords
                    if k.arg in ("translation_key", "failure_key")
                    and isinstance(k.value, ast.Constant)
                }
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                if any(dotted(t) in ("trans_key", "failure_key") for t in node.targets):
                    keys.add(node.value.value)
            elif isinstance(node, ast.arguments):
                named = [*node.posonlyargs, *node.args]
                keys |= {
                    default.value
                    for arg, default in zip(named[len(named) - len(node.defaults) :], node.defaults, strict=True)
                    if arg.arg in ("translation_key", "failure_key") and isinstance(default, ast.Constant)
                }
                keys |= {
                    default.value
                    for arg, default in zip(node.kwonlyargs, node.kw_defaults, strict=True)
                    if arg.arg in ("translation_key", "failure_key") and isinstance(default, ast.Constant)
                }
    return keys


def test_every_exception_key_is_translated() -> None:
    """A key with no strings.json entry shows the user a bare key (§14.9)."""
    missing = _exception_keys_used() - set(_STRINGS["exceptions"])
    assert_frozen(missing, MISSING_EXCEPTION_KEYS, "MISSING_EXCEPTION_KEYS", "add it to strings.json")


def test_every_issue_key_is_translated() -> None:
    """A repair issue with no strings.json entry shows a bare key (§14.9).

    Literal keys, and the string literals of a conditional that picks the key
    (``("cloud_unavailable", ...) if ... else ("cloud_login_failed", ...)``),
    in every module that creates an issue.
    """
    used: set[str] = set()
    for path in source_files():
        tree = parse(path)
        if not any(
            isinstance(n, ast.Call) and dotted(n.func).endswith("async_create_issue")
            for n in ast.walk(tree)
        ):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and dotted(node.func).endswith("async_create_issue"):
                used |= {
                    k.value.value
                    for k in node.keywords
                    if k.arg == "translation_key" and isinstance(k.value, ast.Constant)
                }
            elif isinstance(node, ast.Tuple) and len(node.elts) == 2 and dotted(node.elts[1]).startswith(
                "ir.IssueSeverity."
            ):
                if isinstance(node.elts[0], ast.Constant):
                    used.add(node.elts[0].value)
    assert used, "no issue keys found; the scan no longer matches the code"
    assert used <= set(_STRINGS["issues"]), sorted(used - set(_STRINGS["issues"]))


def _entity_translation_keys() -> dict[str, bool]:
    """Map ``platform.translation_key`` of every literal entity description to whether it has an icon.

    A device class brings its own icon, so it counts as declaring one.
    """
    found: dict[str, bool] = {}
    for path in source_files():
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Call) and dotted(node.func).endswith("EntityDescription"):
                kwargs = {k.arg: k.value for k in node.keywords}
                key = kwargs.get("translation_key", kwargs.get("key"))
                # Without a translation key, a device class names the entity.
                named_by_class = "translation_key" not in kwargs and "device_class" in kwargs
                if isinstance(key, ast.Constant) and not {"name"} & kwargs.keys() and not named_by_class:
                    found[f"{path.stem}.{key.value}"] = bool({"icon", "device_class"} & kwargs.keys())
            elif (
                isinstance(node, ast.Assign)
                and any(dotted(t) == "_attr_translation_key" for t in node.targets)
                and isinstance(node.value, ast.Constant)
            ):
                found[f"{path.stem}.{node.value.value}"] = False
    return found


def test_every_entity_translation_key_is_translated() -> None:
    """An entity key with no strings.json entry is shown untranslated (§14.9)."""
    missing = {
        key
        for key in _entity_translation_keys()
        if key.split(".", 1)[1] not in _STRINGS["entity"].get(key.split(".", 1)[0], {})
    }
    assert_frozen(
        missing, MISSING_ENTITY_TRANSLATIONS, "MISSING_ENTITY_TRANSLATIONS", "add it under entity.<platform>"
    )


def test_every_entity_without_an_icon_has_an_icon_translation() -> None:
    """icons.json carries the icon of every entity that declares none (§14.9)."""
    missing = {
        key
        for key, has_icon in _entity_translation_keys().items()
        if not has_icon and key.split(".", 1)[1] not in _ICONS["entity"].get(key.split(".", 1)[0], {})
    }
    assert_frozen(missing, MISSING_ICONS, "MISSING_ICONS", "add it to icons.json")
