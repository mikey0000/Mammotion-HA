"""Service fields are declared once, with the library's ranges, for every setting the plan holds (§14.11).

edit_task truncated the mower's float speed because the Spino field set also
defined ``speed`` and a ``{**a, **b}`` merge let the later validator win; it
capped ``edge_mode`` at 2 by hand while the library enum goes to 4; and it
left out schedule settings the app edits (#947). Each check below fails when
one of those comes back. The value round trip itself is
``tests_ha/test_services_task_fields.py``.
"""

import ast
import dataclasses
from typing import Any

import pytest
import voluptuous as vol
import yaml
from pymammotion.data.model.hash_list import Plan
from pymammotion.data.model.pool_state import PoolPlan

from custom_components.mammotion import services

from .conventions_support import INTEGRATION, assert_ratchet, parse, rel
from .test_conventions_schemas import _SCHEMAS, _fields

_SCHEMA_MODULES = ("services.py", "lawn_mower.py", "camera.py")

#: Module -> ``vol.In([<int literals>])`` choices still typed by hand.
HAND_TYPED_CHOICES: dict[str, int] = {
    "custom_components/mammotion/lawn_mower.py": 12,
    "custom_components/mammotion/services.py": 1,  # auto_change_direction: an on/off flag, no enum
}

#: (service, field) -> why the schema takes a field services.yaml does not show.
_SCHEMA_ONLY_FIELDS: dict[tuple[str, str], str] = {
    ("start_mow", "rain_tactics"): "retired; still accepted, ignored",
    ("modify_running_job", "rain_tactics"): "retired; still accepted, ignored",
}

#: Plan attribute -> why create_task / edit_task do not take it.
_PLAN_NOT_EDITABLE: dict[str, str] = {
    "pver": "protocol header",
    "sub_cmd": "protocol header",
    "id": "identity",
    "user_id": "identity",
    "device_id": "identity",
    "plan_id": "identity",
    "task_id": "identity",
    "job_id": "identity",
    "job_name": "the app sends task_name",
    "version": "device bookkeeping",
    "area": "device-computed",
    "work_time": "device-computed",
    "required_time": "device-computed",
    "remained_seconds": "device-computed",
    "total_plan_num": "device bookkeeping",
    "plan_index": "device bookkeeping",
    "result": "device reply code",
    "model": "undocumented; an edit re-sends the stored value",
    "week": "derived from weeks by Plan.week_for_send",
    "reserved": (
        "byte 2 is `enabled`; bytes 0-1 (mow order, obstacle laps) need a "
        "pymammotion Plan helper for the echo offset (#947)"
    ),
}

#: Service field -> the Plan attribute it writes, where the names differ.
_PLAN_ATTRIBUTE = {"name": "task_name"}

#: PoolPlan attribute -> why create_task / edit_task do not take it.
_POOL_PLAN_NOT_EDITABLE: dict[str, str] = {
    "cmd": "protocol header",
    "jobid": "identity",
    "userid": "identity",
    "deviceid": "identity",
    "totalplannum": "device bookkeeping",
    "planindex": "device bookkeeping",
    "result": "device reply code",
    "remained_seconds": "device-computed",
}

#: Service field -> the PoolPlan attribute it writes, where the names differ.
_POOL_PLAN_ATTRIBUTE = {
    "name": "jobname",
    "trigger_type": "triggertype",
    "start_date": "startdate",
    "end_date": "enddate",
}


def _edit_task_fields() -> set[str]:
    return set(_fields(services.EDIT_TASK_SCHEMA)) - {"entity_id"}


def _hand_typed_choices(tree: ast.Module) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "In"
        and node.args
        and isinstance(node.args[0], ast.List | ast.Tuple | ast.Set)
        and all(
            isinstance(element, ast.Constant) and type(element.value) is int
            for element in node.args[0].elts
        )
    )


@pytest.mark.parametrize("module", _SCHEMA_MODULES)
def test_no_field_set_is_merged_by_unpacking(module: str) -> None:
    """``{**a, **b}`` lets b's validator for a shared field silently replace a's.

    Merge field sets with ``services._merge_fields``, which refuses a clash.
    """
    path = INTEGRATION / module
    merges = [
        f"{rel(path)}:{node.lineno}"
        for node in ast.walk(parse(path))
        if isinstance(node, ast.Dict) and sum(key is None for key in node.keys) >= 2
    ]
    assert not merges, merges


def test_merge_fields_refuses_a_field_defined_twice() -> None:
    """The mower and Spino field sets once both defined ``speed``."""
    with pytest.raises(ValueError, match="speed"):
        services._merge_fields(
            {vol.Optional("speed"): vol.Coerce(float)},
            {vol.Optional("speed"): vol.Coerce(int)},
        )


def test_choices_come_from_pymammotion_enums() -> None:
    """A range typed by hand drifts from the library (edge_mode 0-2 against 0-4).

    Use ``services._in_enum(<pymammotion enum>)``.
    """
    counts = {
        rel(INTEGRATION / module): count
        for module in _SCHEMA_MODULES
        if (count := _hand_typed_choices(parse(INTEGRATION / module)))
    }
    assert_ratchet(
        counts,
        HAND_TYPED_CHOICES,
        "HAND_TYPED_CHOICES",
        "build the choices from the pymammotion enum (services._in_enum)",
    )


def test_every_schema_field_is_in_services_yaml() -> None:
    """A field missing from services.yaml has no UI, no translation and no example."""
    services_yaml: dict[str, Any] = yaml.safe_load(
        (INTEGRATION / "services.yaml").read_text()
    )
    missing = sorted(
        f"{service}.{field}"
        for service, schema in _SCHEMAS.items()
        for field in set(_fields(schema)) - {"entity_id"}
        if field not in (services_yaml[service].get("fields") or {})
        and (service, field) not in _SCHEMA_ONLY_FIELDS
    )
    assert not missing, missing


def test_the_schema_only_list_has_no_stale_entries() -> None:
    """An allow-listed field that reached services.yaml must leave the list."""
    services_yaml: dict[str, Any] = yaml.safe_load(
        (INTEGRATION / "services.yaml").read_text()
    )
    stale = sorted(
        key
        for key in _SCHEMA_ONLY_FIELDS
        if key[1] in (services_yaml[key[0]].get("fields") or {})
        or key[1] not in _fields(_SCHEMAS[key[0]])
    )
    assert not stale, stale


@pytest.mark.parametrize(
    ("plan_type", "not_editable", "attribute"),
    [
        pytest.param(Plan, _PLAN_NOT_EDITABLE, _PLAN_ATTRIBUTE, id="mower"),
        pytest.param(
            PoolPlan, _POOL_PLAN_NOT_EDITABLE, _POOL_PLAN_ATTRIBUTE, id="spino"
        ),
    ],
)
def test_every_plan_setting_is_editable_or_excluded_with_a_reason(
    plan_type: type[Plan | PoolPlan],
    not_editable: dict[str, str],
    attribute: dict[str, str],
) -> None:
    """A setting the library adds, or the service forgets, fails until someone decides.

    Add it to the schema, get_task and the plan builder, or to the list above
    with the reason it is not editable.
    """
    edited = {attribute.get(field, field) for field in _edit_task_fields()}
    held = {field.name for field in dataclasses.fields(plan_type)}
    undecided = sorted(held - edited - not_editable.keys())
    stale = sorted(not_editable.keys() & edited | not_editable.keys() - held)
    assert not undecided, f"neither editable nor excluded: {undecided}"
    assert not stale, f"excluded but editable, or gone from the library: {stale}"
