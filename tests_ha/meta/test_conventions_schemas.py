"""Service schemas apply no defaults over values an entity or a stored object holds (§14.1–§14.2).

A schema default is filled in on every call that omits the field, so the
handler cannot tell "left out" from "asked for the default": an edit then
overwrites the stored value (edit_task re-enabled disabled schedules, start_mow
reset the config entities, #913). Defaults are allowed only where the list
below says why, and services.yaml may not advertise a default the schema does
not apply.
"""

from collections.abc import Mapping
from typing import Any

import pytest
import voluptuous as vol
import yaml

from custom_components.mammotion import lawn_mower, services

from .conventions_support import INTEGRATION

_SERVICES_YAML = INTEGRATION / "services.yaml"

_SCHEMAS: dict[str, Mapping[Any, Any] | vol.Schema] = {
    lawn_mower.SERVICE_START_MOWING: lawn_mower.START_MOW_SCHEMA,
    lawn_mower.SERVICE_MODIFY_RUNNING_JOB: lawn_mower.MODIFY_RUNNING_JOB_SCHEMA,
    lawn_mower.SERVICE_START_STOP_BLADES: lawn_mower.START_STOP_BLADES_SCHEMA,
    lawn_mower.SERVICE_SET_NON_WORK_HOURS: lawn_mower.SET_NON_WORK_HOURS_SCHEMA,
    lawn_mower.SERVICE_SET_BLADE_WARNING_TIME: lawn_mower.SET_BLADE_WARNING_TIME_SCHEMA,
    lawn_mower.SERVICE_SET_BLADE_HEIGHT: lawn_mower.SET_BLADE_HEIGHT_SCHEMA,
    services.SERVICE_CREATE_TASK: services.CREATE_TASK_SCHEMA,
    services.SERVICE_EDIT_TASK: services.EDIT_TASK_SCHEMA,
    services.SERVICE_RENAME_TASK: services.RENAME_TASK_SCHEMA,
    services.SERVICE_SET_TASK_ENABLED: services.SET_TASK_ENABLED_SCHEMA,
    services.SERVICE_DELETE_TASK: services.DELETE_TASK_SCHEMA,
    services.SERVICE_COPY_TASK: services.COPY_TASK_SCHEMA,
    services.SERVICE_REFRESH_TASKS: services.REFRESH_TASKS_SCHEMA,
    services.SERVICE_START_TASK: services.START_TASK_SCHEMA,
    services.SERVICE_GET_TASKS: services.GET_TASKS_SCHEMA,
    services.SERVICE_GET_TASK: services.GET_TASK_SCHEMA,
    services.SERVICE_BACKUP_MAP: services.BACKUP_MAP_SCHEMA,
    services.SERVICE_RESTORE_MAP: services.MAP_BACKUP_ID_SCHEMA,
    services.SERVICE_DELETE_MAP_BACKUP: services.MAP_BACKUP_ID_SCHEMA,
    services.SERVICE_GET_MAP_BACKUP_PROGRESS: services.MAP_BACKUP_JOB_SCHEMA,
    services.SERVICE_CANCEL_MAP_BACKUP: services.MAP_BACKUP_JOB_SCHEMA,
    services.SERVICE_SVG_ADD: services.SVG_ADD_SCHEMA,
    services.SERVICE_SVG_UPDATE: services.SVG_UPDATE_SCHEMA,
    services.SERVICE_SVG_DELETE: services.SVG_DELETE_SCHEMA,
    services.SERVICE_GET_MAP_DATA: services.MOWER_TARGET_SCHEMA,
    services.SERVICE_GET_GEOJSON: services.MOWER_TARGET_SCHEMA,
    services.SERVICE_GET_MOW_PATH_GEOJSON: services.MOWER_TARGET_SCHEMA,
    services.SERVICE_GET_MOW_PROGRESS_GEOJSON: services.MOWER_TARGET_SCHEMA,
    services.SERVICE_FETCH_MOW_PATH: services.MOWER_TARGET_SCHEMA,
}

#: (service, field) -> why a default cannot overwrite anything held elsewhere.
_ALLOWED_DEFAULTS: dict[tuple[str, str], str] = {
    ("create_task", "enabled"): "a new schedule starts enabled",
    ("svg_add", "svg_file_name"): "a new tile",
    ("svg_add", "scale"): "a new tile",
    ("svg_add", "rotate"): "a new tile",
    ("svg_add", "base_width_m"): "a new tile",
    ("svg_add", "base_height_m"): "a new tile",
    ("start_stop_blades", "start_stop"): "the call's own verb, held nowhere",
    ("get_map_backup_progress", "restore"): "picks which job to ask about",
    ("cancel_map_backup", "restore"): "picks which job to cancel",
}


def _fields(schema: Mapping[Any, Any] | vol.Schema) -> dict[str, vol.Marker]:
    raw = schema.schema if isinstance(schema, vol.Schema) else schema
    return {str(key): key for key in raw if isinstance(key, vol.Marker)}


def _default(marker: vol.Marker) -> Any:
    return marker.default() if marker.default is not vol.UNDEFINED else vol.UNDEFINED


@pytest.fixture(scope="module")
def services_yaml() -> dict[str, Any]:
    """Return the parsed services.yaml."""
    return yaml.safe_load(_SERVICES_YAML.read_text())


def test_every_service_with_fields_is_covered(services_yaml: dict[str, Any]) -> None:
    """A new service must be added to _SCHEMAS so its defaults are checked."""
    with_fields = {name for name, svc in services_yaml.items() if svc.get("fields")}
    assert with_fields <= _SCHEMAS.keys(), sorted(with_fields - _SCHEMAS.keys())


def test_no_schema_default_outside_the_allow_list() -> None:
    """Only the allow-listed fields may be filled in when a call omits them."""
    offenders = sorted(
        f"{service}.{name}={_default(marker)!r}"
        for service, schema in _SCHEMAS.items()
        for name, marker in _fields(schema).items()
        if _default(marker) is not vol.UNDEFINED
        and (service, name) not in _ALLOWED_DEFAULTS
    )
    assert not offenders, offenders


def test_the_allow_list_has_no_stale_entries() -> None:
    """An allow-listed field that lost its default must leave the list."""
    stale = sorted(
        key
        for key in _ALLOWED_DEFAULTS
        if (marker := _fields(_SCHEMAS[key[0]]).get(key[1])) is None
        or _default(marker) is vol.UNDEFINED
    )
    assert not stale, stale


def test_services_yaml_defaults_match_the_schema(
    services_yaml: dict[str, Any],
) -> None:
    """A UI default must be what the schema applies, or prefill a required field."""
    mismatches = []
    for service, svc in services_yaml.items():
        for name, spec in (svc.get("fields") or {}).items():
            if "default" not in spec:
                continue
            marker = _fields(_SCHEMAS[service]).get(name)
            if marker is None:
                mismatches.append(f"{service}.{name}: not in the schema")
            elif (default := _default(marker)) is not vol.UNDEFINED:
                if default != spec["default"]:
                    mismatches.append(
                        f"{service}.{name}: yaml {spec['default']!r} != {default!r}"
                    )
            elif not isinstance(marker, vol.Required):
                mismatches.append(
                    f"{service}.{name}: yaml {spec['default']!r}, schema none"
                )
    assert not mismatches, mismatches
