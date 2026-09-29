"""Schedules carry the per-job "boundary ride distance" through create_task and edit_task.

pymammotion gates the value on the model and border laps when it sends the
schedule, so the service layer passes it through untouched.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import Plan
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.services import (
    CREATE_TASK_SCHEMA,
    EDIT_TASK_SCHEMA,
    SERVICE_GET_TASK,
    async_setup_services,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_MOWER = "Luba-VS1000001"
_PLAN_ID = "202405201230451234567"
_FIELD = "ride_boundary_distance"
_SERVICES = ("create_task", "edit_task")


def _setup(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, plan: Plan
) -> tuple[MagicMock, str, str]:
    """Wire a mower holding ``plan``; return its coordinator, lawn_mower and task button ids."""
    device = MowingDevice()
    device.map.plan[plan.plan_id] = plan
    coordinator = MagicMock()
    coordinator.unique_name = _MOWER
    coordinator.data = device
    coordinator.running_plan = None
    coordinator.async_create_mower_task = AsyncMock()
    coordinator.async_edit_mower_task = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        mowers=[MagicMock(reporting_coordinator=coordinator)], spino=[]
    )
    mower = entity_registry.async_get_or_create(
        "lawn_mower", DOMAIN, _MOWER, config_entry=entry
    ).entity_id
    button = entity_registry.async_get_or_create(
        "button", DOMAIN, f"{_MOWER}_{plan.plan_id}", config_entry=entry
    ).entity_id
    async_setup_services(hass)
    return coordinator, mower, button


async def test_create_task_puts_the_distance_on_the_plan(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """A value other than 0/0.5 reaches the coordinator exactly as given."""
    coordinator, mower, _ = _setup(hass, entity_registry, Plan(plan_id=_PLAN_ID))

    await hass.services.async_call(
        DOMAIN,
        "create_task",
        {"entity_id": mower, "name": "Edges", "edge_mode": 1, _FIELD: 0.2},
        blocking=True,
    )

    (plan,) = coordinator.async_create_mower_task.await_args.args
    assert plan.ride_boundary_distance == 0.2


async def test_edit_task_without_the_field_keeps_the_stored_value(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Editing another setting must not reset the distance to the Plan default."""
    stored = Plan(plan_id=_PLAN_ID, task_name="Front", ride_boundary_distance=0.3)
    coordinator, _, button = _setup(hass, entity_registry, stored)

    await hass.services.async_call(
        DOMAIN, "edit_task", {"entity_id": button, "knife_height": 50}, blocking=True
    )

    (plan,) = coordinator.async_edit_mower_task.await_args.args
    assert plan.knife_height == 50
    assert plan.ride_boundary_distance == 0.3


async def test_edit_task_with_the_field_replaces_the_stored_value(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The edit path applies a supplied distance over the stored one."""
    stored = Plan(plan_id=_PLAN_ID, task_name="Front", ride_boundary_distance=0.3)
    coordinator, _, button = _setup(hass, entity_registry, stored)

    await hass.services.async_call(
        DOMAIN, "edit_task", {"entity_id": button, _FIELD: 0.7}, blocking=True
    )

    (plan,) = coordinator.async_edit_mower_task.await_args.args
    assert plan.ride_boundary_distance == 0.7


@pytest.mark.parametrize("schema", [CREATE_TASK_SCHEMA, EDIT_TASK_SCHEMA])
@pytest.mark.parametrize("value", [1.5, -0.1])
def test_the_schemas_reject_out_of_range_values(
    schema: vol.Schema, value: float
) -> None:
    """The distance is a fraction from 0 to 1."""
    with pytest.raises(vol.Invalid):
        schema({"entity_id": "button.x", "name": "Front", _FIELD: value})


@pytest.mark.parametrize("schema", [CREATE_TASK_SCHEMA, EDIT_TASK_SCHEMA])
def test_the_schemas_coerce_the_value_to_float(schema: vol.Schema) -> None:
    """A slider or YAML may send an int or a string."""
    validated = schema({"entity_id": "button.x", "name": "Front", _FIELD: "1"})
    assert validated[_FIELD] == 1.0
    assert isinstance(validated[_FIELD], float)


async def test_get_task_reports_the_distance(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The serializer shares edit_task's field name so the row feeds back into it."""
    stored = Plan(
        plan_id=_PLAN_ID,
        task_name="Front",
        knife_height=45,
        ride_boundary_distance=0.3,
    )
    _, mower, _ = _setup(hass, entity_registry, stored)

    task = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASK,
        {"entity_id": mower, "task_id": _PLAN_ID},
        blocking=True,
        return_response=True,
    )

    assert task[_FIELD] == 0.3
    assert EDIT_TASK_SCHEMA(task)[_FIELD] == 0.3


@pytest.mark.parametrize("service", _SERVICES)
def test_services_yaml_declares_the_field(service: str) -> None:
    """An optional 0..1 number, like the neighbouring numeric fields."""
    fields = yaml.safe_load((_ROOT / "services.yaml").read_text())[service]["fields"]
    field: dict[str, Any] = fields[_FIELD]
    assert field["required"] is False
    number = field["selector"]["number"]
    assert (number["min"], number["max"]) == (0, 1)
    assert number["step"] == 0.1


def test_the_field_is_translated_in_every_locale() -> None:
    """strings.json plus every locale, in each locale's own language."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    for path in files:
        strings = json.loads(path.read_text(encoding="utf-8"))
        for service in _SERVICES:
            field = strings["services"][service]["fields"][_FIELD]
            assert field["name"] and field["description"], (path, service)
            if path.stem not in ("en", "strings"):
                en_field = english["services"][service]["fields"][_FIELD]
                assert field["name"] != en_field["name"], (path, service)
                assert field["description"] != en_field["description"], (path, service)
