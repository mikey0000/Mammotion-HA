"""``get_task`` reads one stored mower schedule so a script can edit it.

The response uses edit_task's field names, and ``get_tasks`` shares the same
per-task shape, so either can be fed back into edit_task.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import Plan
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.services import (
    EDIT_TASK_SCHEMA,
    SERVICE_GET_TASK,
    SERVICE_GET_TASKS,
    async_setup_services,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_MOWER = "Luba-VS1000001"
#: Real plan ids are 21 digits, beyond JS Number.MAX_SAFE_INTEGER as an int.
_PLAN_ID = "202405201230451234567"
_OTHER_PLAN_ID = "202405201230459999999"
_ZONE_HASH = 10123456789012345

_EDIT_FIELDS = {
    "name",
    "enabled",
    "weeks",
    "start_time",
    "end_time",
    "start_date",
    "end_date",
    "trigger_type",
    "day",
    "knife_height",
    "speed",
    "edge_mode",
    "route_angle",
    "route_spacing",
    "zone_hashs",
}


def _plan(plan_id: str = _PLAN_ID, **kwargs: Any) -> Plan:
    """Return a mower schedule as the device stores it."""
    defaults: dict[str, Any] = {
        "task_name": "Front lawn",
        "start_time": "08:30",
        "end_time": "11:00",
        "weeks": [1, 3, 5],
        "knife_height": 45,
        "speed": 0.4,
        "edge_mode": 1,
        "route_angle": 90,
        "route_spacing": 25,
        "zone_hashs": [_ZONE_HASH],
    }
    return Plan(plan_id=plan_id, **{**defaults, **kwargs})


def _setup(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    *plans: Plan,
    running: Plan | None = None,
) -> tuple[str, str]:
    """Wire a mower holding ``plans``; return its lawn_mower and task button ids."""
    device = MowingDevice()
    for plan in plans:
        device.map.plan[plan.plan_id] = plan
    coordinator = MagicMock()
    coordinator.unique_name = _MOWER
    coordinator.data = device
    coordinator.running_plan = running
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        mowers=[MagicMock(reporting_coordinator=coordinator)], spino=[]
    )
    mower = entity_registry.async_get_or_create(
        "lawn_mower", DOMAIN, _MOWER, config_entry=entry
    ).entity_id
    button = entity_registry.async_get_or_create(
        "button", DOMAIN, f"{_MOWER}_{_PLAN_ID}", config_entry=entry
    ).entity_id
    async_setup_services(hass)
    return mower, button


async def _get_task(hass: HomeAssistant, target: Any, task_id: Any) -> dict[str, Any]:
    """Call get_task and return its response."""
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASK,
        {"entity_id": target, "task_id": task_id},
        blocking=True,
        return_response=True,
    )


async def test_a_found_task_returns_edit_task_fields(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Every setting edit_task can change comes back under the same name."""
    mower, button = _setup(hass, entity_registry, _plan(), _plan(_OTHER_PLAN_ID))

    task = await _get_task(hass, mower, _PLAN_ID)

    assert task.keys() >= _EDIT_FIELDS
    assert task["task_id"] == _PLAN_ID
    assert task["entity_id"] == button
    assert task["name"] == "Front lawn"
    assert task["enabled"] is True
    assert task["running"] is False
    assert task["start_time"] == "08:30"
    assert task["weeks"] == [1, 3, 5]
    assert task["knife_height"] == 45
    assert task["route_angle"] == 90
    assert task["zone_hashs"] == [str(_ZONE_HASH)]
    assert hass.services.supports_response(DOMAIN, SERVICE_GET_TASK) is (
        SupportsResponse.ONLY
    )


async def test_the_response_validates_as_edit_task_input(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Stringified hashes coerce back, and the button entity_id is the target."""
    mower, button = _setup(hass, entity_registry, _plan())

    task = await _get_task(hass, mower, _PLAN_ID)
    validated = EDIT_TASK_SCHEMA(task)

    assert validated["entity_id"] == button
    assert validated["zone_hashs"] == [_ZONE_HASH]


async def test_an_unknown_task_id_raises_the_translated_error(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """A typo in an automation must say which task and mower it looked for."""
    mower, _ = _setup(hass, entity_registry, _plan())

    with pytest.raises(HomeAssistantError) as err:
        await _get_task(hass, mower, "nope")

    assert err.value.translation_key == "task_id_not_found"
    assert err.value.translation_placeholders == {
        "task_id": "nope",
        "entity_id": mower,
    }


async def test_a_ui_target_list_works(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The UI sends the target as a one-element list."""
    mower, _ = _setup(hass, entity_registry, _plan())

    task = await _get_task(hass, [mower], _PLAN_ID)

    assert task["task_id"] == _PLAN_ID


async def test_the_running_flag_marks_only_the_running_task(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The coordinator knows which stored plan the current job came from."""
    plan = _plan()
    other = _plan(_OTHER_PLAN_ID)
    mower, _ = _setup(hass, entity_registry, plan, other, running=plan)

    assert (await _get_task(hass, mower, _PLAN_ID))["running"] is True
    assert (await _get_task(hass, mower, _OTHER_PLAN_ID))["running"] is False


async def test_get_tasks_shares_the_get_task_shape(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """One helper builds both, so the list rows carry the new fields too."""
    plan = _plan()
    mower, button = _setup(hass, entity_registry, plan, running=plan)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASKS,
        {"entity_id": [mower]},
        blocking=True,
        return_response=True,
    )

    (task,) = response["tasks"]
    assert task == await _get_task(hass, mower, _PLAN_ID)
    assert task["running"] is True
    assert task["entity_id"] == button


def test_the_service_is_declared_for_mowers() -> None:
    """Targets a lawn_mower like the other mower services, with a text task_id."""
    service = yaml.safe_load((_ROOT / "services.yaml").read_text())[SERVICE_GET_TASK]
    assert service["target"]["entity"] == {
        "integration": "mammotion",
        "domain": "lawn_mower",
    }
    field = service["fields"]["task_id"]
    assert field["required"] is True
    assert "text" in field["selector"]
    assert field["example"]


def test_the_error_and_service_are_translated_everywhere() -> None:
    """strings.json plus every locale under translations/."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    for path in files:
        strings = json.loads(path.read_text(encoding="utf-8"))
        service = strings["services"][SERVICE_GET_TASK]
        message = strings["exceptions"]["task_id_not_found"]["message"]
        assert service["name"] and service["description"], path
        assert service["fields"]["task_id"]["name"], path
        assert "{task_id}" in message and "{entity_id}" in message, path
        if path.stem not in ("en", "strings"):
            assert (
                service["description"]
                != english["services"][SERVICE_GET_TASK]["description"]
            ), path
