"""Task buttons track ``map.plan``: vanished plans are removed, orphans swept at setup.

The stubbed version handed ``button.py`` a registry double and asserted on the
list of entity_ids it had been asked to remove.  Here the registry is the real
one, so the tests say what the user sees: which rows survive a plan being
deleted, and which are swept when HA comes back up after one vanished.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import Plan

from custom_components.mammotion.button import (
    async_add_task_entities,
    async_remove_entities,
    async_remove_orphaned_task_entities,
    async_setup_entry,
)
from custom_components.mammotion.const import DOMAIN

PLAN_A = "162247123456712345678"
PLAN_B = "162247123456787654321"
_ORPHAN = "17838509183764485777"
_DEVICE = "Luba-VS123456"


def _plan(plan_id: str, task_name: str = "Front lawn") -> Plan:
    return Plan(plan_id=plan_id, task_name=task_name)


def _coordinator(
    hass: HomeAssistant, plans: dict[str, Plan] | None, unique_name: str = _DEVICE
) -> MagicMock:
    """Build a coordinator whose ``data`` is a real device carrying real plans."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.unique_name = unique_name
    coordinator.device_name = _DEVICE
    if plans is None:
        coordinator.data = None
    else:
        coordinator.data = MowingDevice()
        coordinator.data.map.plan = plans
    return coordinator


def _register(
    hass: HomeAssistant,
    domain: str,
    unique_id: str,
    object_id: str,
) -> str:
    """Seed the registry with a row from a previous session."""
    return (
        er.async_get(hass)
        .async_get_or_create(domain, DOMAIN, unique_id, suggested_object_id=object_id)
        .entity_id
    )


def _mammotion_entity_ids(hass: HomeAssistant) -> set[str]:
    return {
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN
    }


@pytest.fixture
def add_entities() -> MagicMock:
    """Stand in for the platform's ``async_add_entities``."""
    return MagicMock()


async def test_vanished_task_button_is_removed(
    hass: HomeAssistant, add_entities: MagicMock
) -> None:
    """A plan dropped from map.plan takes its registry row and tracking with it."""
    coordinator = _coordinator(
        hass, {PLAN_A: _plan(PLAN_A), PLAN_B: _plan(PLAN_B, "Back lawn")}
    )
    added: set[str] = set()
    by_id: dict[str, Any] = {}

    async_add_task_entities(coordinator, added, by_id, add_entities)
    assert added == {PLAN_A, PLAN_B}
    assert len(add_entities.call_args.args[0]) == 2
    kept = _register(hass, "button", f"{_DEVICE}_{PLAN_A}", "task_a")
    gone = _register(hass, "button", f"{_DEVICE}_{PLAN_B}", "task_b")

    del coordinator.data.map.plan[PLAN_B]
    async_add_task_entities(coordinator, added, by_id, add_entities)

    assert _mammotion_entity_ids(hass) == {kept}
    assert gone not in _mammotion_entity_ids(hass)
    assert added == {PLAN_A}
    assert set(by_id) == {PLAN_A}
    add_entities.assert_called_once()


async def test_unchanged_tasks_remove_nothing(
    hass: HomeAssistant, add_entities: MagicMock
) -> None:
    """A repeat sync with the same plans must leave the registry alone."""
    coordinator = _coordinator(hass, {PLAN_A: _plan(PLAN_A)})
    added: set[str] = set()
    by_id: dict[str, Any] = {}
    row = _register(hass, "button", f"{_DEVICE}_{PLAN_A}", "task_a")

    async_add_task_entities(coordinator, added, by_id, add_entities)
    async_add_task_entities(coordinator, added, by_id, add_entities)

    assert _mammotion_entity_ids(hass) == {row}
    assert added == {PLAN_A}
    add_entities.assert_called_once()


async def test_plan_key_mismatch_skips_only_that_task(
    hass: HomeAssistant, add_entities: MagicMock
) -> None:
    """A key whose plan carries a different plan_id is dropped without aborting."""
    coordinator = _coordinator(hass, {PLAN_A: _plan(PLAN_A), PLAN_B: _plan("mismatch")})
    added: set[str] = set()
    by_id: dict[str, Any] = {}
    rows = {
        _register(hass, "button", f"{_DEVICE}_{PLAN_A}", "task_a"),
        _register(hass, "button", f"{_DEVICE}_{PLAN_B}", "task_b"),
    }

    async_add_task_entities(coordinator, added, by_id, add_entities)

    assert added == {PLAN_A}
    assert set(coordinator.data.map.plan) == {PLAN_A}
    add_entities.assert_called_once()
    assert len(add_entities.call_args.args[0]) == 1
    # The skipped plan was never tracked, so its row is not swept along with it.
    assert _mammotion_entity_ids(hass) == rows


async def test_remove_entities_looks_up_by_unique_name(
    hass: HomeAssistant,
) -> None:
    """Removal resolves the row by the same unique_id the entity registers."""
    coordinator = _coordinator(hass, {}, unique_name="Unique-1")
    row = _register(hass, "button", f"Unique-1_{PLAN_A}", "task_a")

    async_remove_entities(coordinator, {PLAN_A})

    assert er.async_get(hass).async_get(row) is None


async def test_orphan_sweep_removes_only_stale_task_buttons(
    hass: HomeAssistant,
) -> None:
    """Only this device's button rows with a digit suffix and no plan go.

    Plan ids are 21-digit strings while the static buttons use word keys, and
    the area switch and task-area sensor share the device prefix.
    """
    stale = _register(hass, "button", f"{_DEVICE}_{_ORPHAN}", "stale")
    survivors = {
        _register(hass, "button", f"{_DEVICE}_{PLAN_A}", "current"),
        _register(hass, "button", f"{_DEVICE}_start_map_sync", "static"),
        _register(hass, "button", f"Luba-OTHER_{_ORPHAN}", "other_device"),
        _register(hass, "switch", f"{_DEVICE}_555", "area"),
        _register(hass, "sensor", f"{_DEVICE}_555_task_area", "task_area"),
    }

    async_remove_orphaned_task_entities(_coordinator(hass, {PLAN_A: _plan(PLAN_A)}))

    assert _mammotion_entity_ids(hass) == survivors
    assert stale not in survivors


async def test_orphan_sweep_skipped_without_data(hass: HomeAssistant) -> None:
    """Without coordinator data the plan list is unknown, so nothing is removed."""
    stale = _register(hass, "button", f"{_DEVICE}_{_ORPHAN}", "stale")

    async_remove_orphaned_task_entities(_coordinator(hass, None))

    assert er.async_get(hass).async_get(stale) is not None


async def test_setup_entry_sweeps_orphans_before_first_sync(
    hass: HomeAssistant, add_entities: MagicMock
) -> None:
    """Platform setup removes cross-session orphans and adds the current plans."""
    stale = _register(hass, "button", f"{_DEVICE}_{_ORPHAN}", "stale")
    mower = MagicMock()
    mower.device.device_name = _DEVICE
    mower.reporting_coordinator = _coordinator(hass, {PLAN_A: _plan(PLAN_A)})
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    entry.runtime_data.spino = []

    await async_setup_entry(hass, entry, add_entities)

    assert er.async_get(hass).async_get(stale) is None
    task_entities = add_entities.call_args_list[0].args[0]
    assert [e.entity_description.plan_id for e in task_entities] == [PLAN_A]
