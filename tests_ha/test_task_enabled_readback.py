"""Schedules can be read back, not just written (issue #890).

``set_task_enabled`` writes the flag but nothing surfaced it, so an automation
could not reconcile Home Assistant with the mower or confirm a change landed.
Two additions: the flag as a task-button attribute, and ``get_tasks`` returning
the whole set as a service response.

The version in ``tests/`` matched tokens in ``button.py`` and ``services.py``.
Here the button reads a real ``Plan`` — whose ``is_enabled()`` decodes
``reserved[2]`` — and ``get_tasks`` is called on a real Home Assistant and its
response inspected.  The translation and ``services.yaml`` checks stay as they
were: they are about files, not behaviour.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice, PoolCleanerDevice
from pymammotion.data.model.hash_list import Plan
from pymammotion.data.model.pool_state import PoolPlan
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.button import (
    MammotionTaskButtonSensorEntity,
    MammotionTaskButtonSensorEntityDescription,
)
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.services import SERVICE_GET_TASKS, async_setup_services

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_MOWER = "Luba-VS1000001"
_SPINO = "Spino-E1C36JT4"
_PLAN_ID = "1"
_JOB_ID = 4815162342
#: Beyond JS Number.MAX_SAFE_INTEGER, like every real zone hash.
_ZONE_HASH = 10123456789012345


def _plan(**kwargs: Any) -> Plan:
    """Return a mower schedule as the device stores it."""
    return Plan(plan_id=_PLAN_ID, task_name="Front lawn", **kwargs)


def _task_button(device: MowingDevice | None) -> MammotionTaskButtonSensorEntity:
    """Build the real task button; only the coordinator under it is a stand-in."""
    coordinator = MagicMock()
    coordinator.data = device
    entity = MammotionTaskButtonSensorEntity.__new__(MammotionTaskButtonSensorEntity)
    entity.coordinator = coordinator
    entity.entity_description = MammotionTaskButtonSensorEntityDescription(
        key=_PLAN_ID,
        plan_id=_PLAN_ID,
        press_fn=lambda coordinator, plan_id: coordinator.start_task(plan_id),
    )
    return entity


def _mower_device(plan: Plan) -> MowingDevice:
    """Return a mower holding exactly one schedule."""
    device = MowingDevice()
    device.map.plan[_PLAN_ID] = plan
    return device


def _spino_device() -> PoolCleanerDevice:
    """Return a pool cleaner holding exactly one schedule."""
    device = PoolCleanerDevice()
    device.plans[_JOB_ID] = PoolPlan(
        jobid=_JOB_ID, jobname="Morning clean", enabled=False
    )
    return device


def _register_device(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    unique_name: str,
    *,
    mowers: list[MagicMock] | None = None,
    spino: list[MagicMock] | None = None,
) -> str:
    """Wire one device's coordinator into a config entry and return its entity_id."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(mowers=mowers or [], spino=spino or [])
    return entity_registry.async_get_or_create(
        "button", DOMAIN, f"{unique_name}_{_PLAN_ID}", config_entry=entry
    ).entity_id


def _coordinator(unique_name: str, data: Any) -> MagicMock:
    """Stand in for the device's primary coordinator."""
    coordinator = MagicMock()
    coordinator.unique_name = unique_name
    coordinator.data = data
    return coordinator


def test_the_flag_is_a_property_not_a_snapshot() -> None:
    """Set in __init__ it would report whatever was true at setup."""
    device = _mower_device(_plan())
    button = _task_button(device)
    assert button.extra_state_attributes["enabled"] is True

    device.map.plan[_PLAN_ID] = device.map.plan[_PLAN_ID].with_enabled(False)

    assert button.extra_state_attributes["enabled"] is False


def test_the_flag_reads_the_encoding_the_device_sends_back() -> None:
    """Stored plans come back with +10 on every reserved byte: 10 on, 11 off."""
    disabled = _task_button(_mower_device(_plan(reserved="\x0a\x0a\x0b")))
    enabled = _task_button(_mower_device(_plan(reserved="\x0a\x0a\x0a")))

    assert disabled.extra_state_attributes["enabled"] is False
    assert enabled.extra_state_attributes["enabled"] is True


def test_the_task_id_attribute_is_preserved() -> None:
    """Additive change: anything relying on task_id must keep working."""
    attributes = _task_button(_mower_device(_plan())).extra_state_attributes
    assert attributes["task_id"] == _PLAN_ID


def test_a_missing_plan_yields_no_flag() -> None:
    """``data`` is absent between entity creation and the first refresh."""
    assert _task_button(None).extra_state_attributes == {"task_id": _PLAN_ID}
    assert _task_button(MowingDevice()).extra_state_attributes == {"task_id": _PLAN_ID}


async def test_get_tasks_returns_a_response(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The maintainer's preference: readable in the same script that writes."""
    async_setup_services(hass)
    coordinator = _coordinator(_MOWER, _mower_device(_plan()))
    entity_id = _register_device(
        hass,
        entity_registry,
        _MOWER,
        mowers=[MagicMock(reporting_coordinator=coordinator)],
    )

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASKS,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )

    assert [task["task_id"] for task in response["tasks"]] == [_PLAN_ID]
    assert (
        hass.services.supports_response(DOMAIN, SERVICE_GET_TASKS)
        is SupportsResponse.ONLY
    )


async def test_the_response_reports_the_mower_enable_flag(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Without it the service answers the wrong question."""
    async_setup_services(hass)
    coordinator = _coordinator(_MOWER, _mower_device(_plan().with_enabled(False)))
    entity_id = _register_device(
        hass,
        entity_registry,
        _MOWER,
        mowers=[MagicMock(reporting_coordinator=coordinator)],
    )

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASKS,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )

    assert response["tasks"][0]["enabled"] is False
    assert response["tasks"][0]["name"] == "Front lawn"


async def test_get_tasks_covers_spino_as_well(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Task services target mowers and Spinos alike."""
    async_setup_services(hass)
    coordinator = _coordinator(_SPINO, _spino_device())
    entity_id = _register_device(
        hass, entity_registry, _SPINO, spino=[MagicMock(coordinator=coordinator)]
    )

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASKS,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )

    (task,) = response["tasks"]
    assert task["task_id"] == str(_JOB_ID)
    assert task["name"] == "Morning clean"
    assert task["enabled"] is False


async def test_the_response_task_id_matches_the_button_attribute(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """So a script can line a response row up with the entity it came from."""
    async_setup_services(hass)
    device = _mower_device(_plan())
    coordinator = _coordinator(_MOWER, device)
    entity_id = _register_device(
        hass,
        entity_registry,
        _MOWER,
        mowers=[MagicMock(reporting_coordinator=coordinator)],
    )

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASKS,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )

    button = _task_button(device)
    assert response["tasks"][0]["task_id"] == button.extra_state_attributes["task_id"]


async def test_large_hashes_survive_the_websocket(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """zone_hashs exceed JS Number.MAX_SAFE_INTEGER."""
    async_setup_services(hass)
    coordinator = _coordinator(_MOWER, _mower_device(_plan(zone_hashs=[_ZONE_HASH])))
    entity_id = _register_device(
        hass,
        entity_registry,
        _MOWER,
        mowers=[MagicMock(reporting_coordinator=coordinator)],
    )

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_TASKS,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )

    assert response["tasks"][0]["zone_hashs"] == [str(_ZONE_HASH)]


def test_the_service_is_declared() -> None:
    """services.yaml is what the UI builds the action dialog from."""
    services = yaml.safe_load((_ROOT / "services.yaml").read_text())
    assert SERVICE_GET_TASKS in services
    assert services[SERVICE_GET_TASKS]["target"]["entity"]["integration"] == "mammotion"


def test_the_service_is_translated_everywhere() -> None:
    """strings.json plus every locale under translations/."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    for path in files:
        entry = json.loads(path.read_text())["services"][SERVICE_GET_TASKS]
        assert entry["name"] and entry["description"], path
        if path.stem not in ("en", "strings"):
            assert (
                entry["description"]
                != english["services"][SERVICE_GET_TASKS]["description"]
            ), path


@pytest.mark.parametrize("key", ["name", "description"])
def test_the_english_strings_and_translation_agree(key: str) -> None:
    """strings.json is the source the other locales are generated from."""
    source = json.loads((_ROOT / "strings.json").read_text())
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    assert (
        source["services"][SERVICE_GET_TASKS][key]
        == english["services"][SERVICE_GET_TASKS][key]
    )
