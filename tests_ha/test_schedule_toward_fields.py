"""Schedules carry the path-angle mode and crossing angle through create_task and edit_task.

pymammotion sends ``Plan.toward_mode`` and ``Plan.toward_included_angle`` on
every full plan write, ungated by model as the app does, so the service layer
only has to deliver them and keep the stored values when they are left out.
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
_FIELDS = ("toward_mode", "toward_included_angle")
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


async def test_create_task_puts_the_angles_on_the_plan(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Both fields reach the coordinator exactly as given."""
    coordinator, mower, _ = _setup(hass, entity_registry, Plan(plan_id=_PLAN_ID))

    await hass.services.async_call(
        DOMAIN,
        "create_task",
        {
            "entity_id": mower,
            "name": "Grid",
            "toward_mode": 1,
            "toward_included_angle": 45,
        },
        blocking=True,
    )

    (plan,) = coordinator.async_create_mower_task.await_args.args
    assert (plan.toward_mode, plan.toward_included_angle) == (1, 45)


async def test_edit_task_without_the_fields_keeps_the_stored_values(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Editing another setting must not reset the angles to the Plan defaults."""
    stored = Plan(
        plan_id=_PLAN_ID, task_name="Front", toward_mode=2, toward_included_angle=30
    )
    coordinator, _, button = _setup(hass, entity_registry, stored)

    await hass.services.async_call(
        DOMAIN, "edit_task", {"entity_id": button, "knife_height": 50}, blocking=True
    )

    (plan,) = coordinator.async_edit_mower_task.await_args.args
    assert plan.knife_height == 50
    assert (plan.toward_mode, plan.toward_included_angle) == (2, 30)


async def test_edit_task_with_the_fields_replaces_the_stored_values(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The edit path applies supplied angles over the stored ones."""
    stored = Plan(
        plan_id=_PLAN_ID, task_name="Front", toward_mode=2, toward_included_angle=30
    )
    coordinator, _, button = _setup(hass, entity_registry, stored)

    await hass.services.async_call(
        DOMAIN,
        "edit_task",
        {"entity_id": button, "toward_mode": 0, "toward_included_angle": 90},
        blocking=True,
    )

    (plan,) = coordinator.async_edit_mower_task.await_args.args
    assert (plan.toward_mode, plan.toward_included_angle) == (0, 90)


@pytest.mark.parametrize("schema", [CREATE_TASK_SCHEMA, EDIT_TASK_SCHEMA])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("toward_mode", 3),
        ("toward_mode", -1),
        ("toward_included_angle", 181),
        ("toward_included_angle", -181),
    ],
)
def test_the_schemas_reject_out_of_range_values(
    schema: vol.Schema, field: str, value: int
) -> None:
    """toward_mode is 0/1/2 and the crossing angle stays within ±180, as on start_mow."""
    with pytest.raises(vol.Invalid):
        schema({"entity_id": "button.x", "name": "Front", field: value})


@pytest.mark.parametrize("schema", [CREATE_TASK_SCHEMA, EDIT_TASK_SCHEMA])
def test_the_schemas_coerce_the_values_to_int(schema: vol.Schema) -> None:
    """A UI select or YAML may send strings."""
    validated = schema(
        {
            "entity_id": "button.x",
            "name": "Front",
            "toward_mode": "2",
            "toward_included_angle": "45",
        }
    )
    assert (validated["toward_mode"], validated["toward_included_angle"]) == (2, 45)


async def test_get_task_reports_the_angles(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The serializer shares edit_task's field names so the row feeds back into it."""
    stored = Plan(
        plan_id=_PLAN_ID,
        task_name="Front",
        knife_height=45,
        toward_mode=1,
        toward_included_angle=60,
    )
    _, mower, _ = _setup(hass, entity_registry, stored)

    task = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASK,
        {"entity_id": mower, "task_id": _PLAN_ID},
        blocking=True,
        return_response=True,
    )

    assert (task["toward_mode"], task["toward_included_angle"]) == (1, 60)
    validated = EDIT_TASK_SCHEMA(task)
    assert (validated["toward_mode"], validated["toward_included_angle"]) == (1, 60)


@pytest.mark.parametrize("service", _SERVICES)
def test_services_yaml_declares_the_fields(service: str) -> None:
    """Optional, bounded like start_mow's fields of the same name."""
    fields = yaml.safe_load((_ROOT / "services.yaml").read_text())[service]["fields"]
    mode: dict[str, Any] = fields["toward_mode"]
    assert mode["required"] is False
    assert mode["selector"]["select"]["options"] == ["0", "1", "2"]
    assert mode["selector"]["select"]["translation_key"] == "toward_mode"
    angle = fields["toward_included_angle"]
    assert angle["required"] is False
    number = angle["selector"]["number"]
    assert (number["min"], number["max"]) == (-180, 180)


def test_the_fields_are_translated_in_every_locale() -> None:
    """strings.json plus every locale, in each locale's own language."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 2
    for path in files:
        strings = json.loads(path.read_text(encoding="utf-8"))
        for service in _SERVICES:
            for name in _FIELDS:
                field = strings["services"][service]["fields"][name]
                assert field["name"] and field["description"], (path, service, name)
                if path.stem not in ("en", "strings"):
                    en_field = english["services"][service]["fields"][name]
                    assert field["name"] != en_field["name"], (path, service, name)
                    assert field["description"] != en_field["description"], (
                        path,
                        service,
                        name,
                    )
