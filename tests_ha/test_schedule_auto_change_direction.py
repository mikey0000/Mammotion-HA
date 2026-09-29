"""Schedules carry "auto-reverse mowing direction" through create_task and edit_task.

pymammotion sends the schedule's field with no model gate, so the service layer
refuses to switch it on where the mower or its firmware does not offer it.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
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
_SUPPORTED = "Luba-VA1000001"
_FIRMWARE = "2.3.28.1"
_PLAN_ID = "202405201230451234567"
_FIELD = "auto_change_direction"
_SERVICES = ("create_task", "edit_task")
_EXCEPTION = "auto_change_direction_unsupported"

# Luba 1, Luba 2, Luba 2 Pro and the original Yuka never offer it; others need 2.3.28.1.
_UNSUPPORTED = [
    ("Luba-1000001", _FIRMWARE),
    ("Luba-VS1000001", _FIRMWARE),
    ("Luba-VP1000001", _FIRMWARE),
    ("Yuka-1000001", _FIRMWARE),
    (_SUPPORTED, "2.3.28.0"),
    (_SUPPORTED, ""),
]


def _setup(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    plan: Plan,
    name: str = _SUPPORTED,
    firmware: str = _FIRMWARE,
) -> tuple[MagicMock, str, str]:
    """Wire a mower holding ``plan``; return its coordinator, lawn_mower and task button ids."""
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    device.map.plan[plan.plan_id] = plan
    coordinator = MagicMock()
    coordinator.unique_name = name
    coordinator.device_name = name
    coordinator.device.product_key = ""
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
        "lawn_mower", DOMAIN, name, config_entry=entry
    ).entity_id
    button = entity_registry.async_get_or_create(
        "button", DOMAIN, f"{name}_{plan.plan_id}", config_entry=entry
    ).entity_id
    async_setup_services(hass)
    return coordinator, mower, button


async def test_create_task_puts_the_setting_on_the_plan(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """A supported mower on new enough firmware gets the setting as a bool."""
    coordinator, mower, _ = _setup(hass, entity_registry, Plan(plan_id=_PLAN_ID))

    await hass.services.async_call(
        DOMAIN,
        "create_task",
        {"entity_id": mower, "name": "Front", _FIELD: 1},
        blocking=True,
    )

    (plan,) = coordinator.async_create_mower_task.await_args.args
    assert plan.auto_change_direction is True


async def test_create_task_without_the_field_leaves_it_unset(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Omitted stays None, which the library sends as off."""
    coordinator, mower, _ = _setup(hass, entity_registry, Plan(plan_id=_PLAN_ID))

    await hass.services.async_call(
        DOMAIN, "create_task", {"entity_id": mower, "name": "Front"}, blocking=True
    )

    (plan,) = coordinator.async_create_mower_task.await_args.args
    assert plan.auto_change_direction is None


async def test_edit_task_without_the_field_keeps_the_stored_value(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Editing another setting must not reset the stored setting."""
    stored = Plan(plan_id=_PLAN_ID, task_name="Front", auto_change_direction=True)
    coordinator, _, button = _setup(hass, entity_registry, stored)

    await hass.services.async_call(
        DOMAIN, "edit_task", {"entity_id": button, "knife_height": 50}, blocking=True
    )

    (plan,) = coordinator.async_edit_mower_task.await_args.args
    assert plan.knife_height == 50
    assert plan.auto_change_direction is True


async def test_edit_task_with_off_replaces_the_stored_value(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The edit path applies a supplied off over a stored on."""
    stored = Plan(plan_id=_PLAN_ID, task_name="Front", auto_change_direction=True)
    coordinator, _, button = _setup(hass, entity_registry, stored)

    await hass.services.async_call(
        DOMAIN, "edit_task", {"entity_id": button, _FIELD: 0}, blocking=True
    )

    (plan,) = coordinator.async_edit_mower_task.await_args.args
    assert plan.auto_change_direction is False


@pytest.mark.parametrize(("name", "firmware"), _UNSUPPORTED)
@pytest.mark.parametrize("service", _SERVICES)
async def test_switching_it_on_where_unsupported_is_refused(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    service: str,
    name: str,
    firmware: str,
) -> None:
    """The library has no gate, so nothing may be sent for these mowers."""
    coordinator, mower, button = _setup(
        hass, entity_registry, Plan(plan_id=_PLAN_ID), name, firmware
    )
    target = mower if service == "create_task" else button

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            service,
            {"entity_id": target, "name": "Front", _FIELD: 1},
            blocking=True,
        )

    assert err.value.translation_key == _EXCEPTION
    coordinator.async_create_mower_task.assert_not_awaited()
    coordinator.async_edit_mower_task.assert_not_awaited()


@pytest.mark.parametrize("service", _SERVICES)
async def test_switching_it_off_where_unsupported_is_sent(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, service: str
) -> None:
    """Off is what the library sends there anyway, so it is never refused."""
    coordinator, mower, button = _setup(
        hass, entity_registry, Plan(plan_id=_PLAN_ID), "Luba-VS1000001"
    )
    target = mower if service == "create_task" else button

    await hass.services.async_call(
        DOMAIN,
        service,
        {"entity_id": target, "name": "Front", _FIELD: 0},
        blocking=True,
    )

    sent = (
        coordinator.async_create_mower_task
        if service == "create_task"
        else coordinator.async_edit_mower_task
    )
    (plan,) = sent.await_args.args
    assert plan.auto_change_direction is False


@pytest.mark.parametrize("schema", [CREATE_TASK_SCHEMA, EDIT_TASK_SCHEMA])
@pytest.mark.parametrize(("value", "expected"), [("1", 1), (0.0, 0), (True, 1)])
def test_the_schemas_coerce_like_start_mow(
    schema: vol.Schema, value: Any, expected: int
) -> None:
    """The 0/1 style of start_mow's field, from a slider, YAML string or bool."""
    validated = schema({"entity_id": "button.x", "name": "Front", _FIELD: value})
    assert validated[_FIELD] == expected
    assert type(validated[_FIELD]) is int


@pytest.mark.parametrize("schema", [CREATE_TASK_SCHEMA, EDIT_TASK_SCHEMA])
def test_the_schemas_reject_other_values(schema: vol.Schema) -> None:
    """Only off and on exist."""
    with pytest.raises(vol.Invalid):
        schema({"entity_id": "button.x", "name": "Front", _FIELD: 2})


@pytest.mark.parametrize("stored", [True, False, None])
async def test_get_task_reports_the_setting_and_feeds_edit_task(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, stored: bool | None
) -> None:
    """The serializer shares edit_task's field name so the row feeds back into it."""
    plan = Plan(
        plan_id=_PLAN_ID,
        task_name="Front",
        knife_height=45,
        auto_change_direction=stored,
    )
    _, mower, _ = _setup(hass, entity_registry, plan)

    task = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASK,
        {"entity_id": mower, "task_id": _PLAN_ID},
        blocking=True,
        return_response=True,
    )

    assert task[_FIELD] is stored
    assert EDIT_TASK_SCHEMA(task).get(_FIELD) == stored


@pytest.mark.parametrize("service", _SERVICES)
def test_services_yaml_declares_the_field(service: str) -> None:
    """Optional and without a default, so an edit never overwrites the stored value."""
    fields = yaml.safe_load((_ROOT / "services.yaml").read_text())[service]["fields"]
    field: dict[str, Any] = fields[_FIELD]
    assert field["required"] is False
    assert "default" not in field
    number = field["selector"]["number"]
    assert (number["min"], number["max"], number["step"]) == (0, 1, 1)


def _locales() -> list[Path]:
    """Return strings.json and every translation file."""
    return [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]


def test_the_field_is_translated_in_every_locale() -> None:
    """strings.json plus every locale, in each locale's own language."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    for path in _locales():
        strings = json.loads(path.read_text(encoding="utf-8"))
        for service in _SERVICES:
            field = strings["services"][service]["fields"][_FIELD]
            assert field["name"] and field["description"], (path, service)
            if path.stem not in ("en", "strings"):
                en_field = english["services"][service]["fields"][_FIELD]
                assert field["name"] != en_field["name"], (path, service)
                assert field["description"] != en_field["description"], (path, service)


def test_the_refusal_is_translated_in_every_locale() -> None:
    """The error names the mower, in each locale's own language."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    en_message = english["exceptions"][_EXCEPTION]["message"]
    for path in _locales():
        strings = json.loads(path.read_text(encoding="utf-8"))
        message = strings["exceptions"][_EXCEPTION]["message"]
        assert "{device_name}" in message, path
        if path.stem not in ("en", "strings"):
            assert message != en_message, path
