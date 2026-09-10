"""Task buttons track ``map.plan``: vanished plans are removed, orphans swept at setup."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


def _stub(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


@dataclass(frozen=True, kw_only=True)
class _ButtonEntityDescription:
    key: str = ""
    name: str | None = None
    entity_category: object = None
    translation_key: str | None = None
    translation_placeholders: dict | None = None


class _ButtonEntity:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


def _load_button() -> types.ModuleType:
    """Load the real ``button.py`` by path, stubbing the imports conftest does not."""
    if "homeassistant.components.button" not in sys.modules:
        _stub(
            "homeassistant.components.button",
            DOMAIN="button",
            ButtonEntity=_ButtonEntity,
            ButtonEntityDescription=_ButtonEntityDescription,
        )
    hash_list = sys.modules["pymammotion.data.model.hash_list"]
    if not hasattr(hash_list, "Plan"):
        hash_list.Plan = object
    pool_state = sys.modules["pymammotion.data.model.pool_state"]
    if not hasattr(pool_state, "PoolPlan"):
        pool_state.PoolPlan = object

    path = (
        Path(__file__).parent.parent / "custom_components" / "mammotion" / "button.py"
    )
    spec = importlib.util.spec_from_file_location(
        "custom_components.mammotion.button", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_components.mammotion.button"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def button_module() -> types.ModuleType:
    """Load button.py once per module."""
    return _load_button()


class _Registry:
    """Entity registry double exposing the calls button.py makes."""

    def __init__(self, entries: list[SimpleNamespace] | None = None) -> None:
        self.entities = {e.entity_id: e for e in entries or []}
        self.removed: list[str] = []
        self.lookups: list[tuple[str, str, str]] = []

    def async_get_entity_id(
        self, domain: str, platform: str, unique_id: str
    ) -> str | None:
        self.lookups.append((domain, platform, unique_id))
        for entry in self.entities.values():
            if (entry.domain, entry.platform, entry.unique_id) == (
                domain,
                platform,
                unique_id,
            ):
                return entry.entity_id
        return None

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.entities.pop(entity_id, None)


def _entry(entity_id: str, unique_id: str, domain: str = "button") -> SimpleNamespace:
    return SimpleNamespace(
        entity_id=entity_id, unique_id=unique_id, domain=domain, platform="mammotion"
    )


def _plan(plan_id: str, task_name: str = "Front lawn") -> SimpleNamespace:
    return SimpleNamespace(plan_id=plan_id, task_name=task_name)


def _coordinator(
    plans: dict[str, Any] | None, unique_name: str = "Luba-TEST"
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.unique_name = unique_name
    coordinator.device_name = "Luba-TEST"
    if plans is None:
        coordinator.data = None
    else:
        coordinator.data.map.plan = plans
    return coordinator


@pytest.fixture
def registry(
    button_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> _Registry:
    """Point ``er.async_get`` at a fresh registry double."""
    reg = _Registry()
    monkeypatch.setattr(button_module.er, "async_get", MagicMock(return_value=reg))
    return reg


PLAN_A = "162247123456712345678"
PLAN_B = "162247123456787654321"


def test_vanished_task_button_is_removed(
    button_module: types.ModuleType, registry: _Registry
) -> None:
    """A plan dropped from map.plan removes its button and tracking state."""
    coordinator = _coordinator(
        {PLAN_A: _plan(PLAN_A), PLAN_B: _plan(PLAN_B, "Back lawn")}
    )
    added: set[str] = set()
    by_id: dict[str, Any] = {}
    add_entities = MagicMock()

    button_module.async_add_task_entities(coordinator, added, by_id, add_entities)
    assert added == {PLAN_A, PLAN_B}
    assert len(add_entities.call_args.args[0]) == 2
    registry.entities = {
        "button.a": _entry("button.a", f"Luba-TEST_{PLAN_A}"),
        "button.b": _entry("button.b", f"Luba-TEST_{PLAN_B}"),
    }

    del coordinator.data.map.plan[PLAN_B]
    button_module.async_add_task_entities(coordinator, added, by_id, add_entities)

    assert registry.removed == ["button.b"]
    assert added == {PLAN_A}
    assert set(by_id) == {PLAN_A}
    add_entities.assert_called_once()


def test_unchanged_tasks_remove_nothing(
    button_module: types.ModuleType, registry: _Registry
) -> None:
    """A repeat sync with the same plans never touches the registry."""
    coordinator = _coordinator({PLAN_A: _plan(PLAN_A)})
    added: set[str] = set()
    by_id: dict[str, Any] = {}
    add_entities = MagicMock()

    button_module.async_add_task_entities(coordinator, added, by_id, add_entities)
    button_module.async_add_task_entities(coordinator, added, by_id, add_entities)

    assert registry.removed == []
    assert registry.lookups == []
    assert added == {PLAN_A}
    add_entities.assert_called_once()


def test_plan_key_mismatch_skips_only_that_task(
    button_module: types.ModuleType, registry: _Registry
) -> None:
    """A key whose plan carries a different plan_id is dropped without aborting."""
    coordinator = _coordinator({PLAN_A: _plan(PLAN_A), PLAN_B: _plan("mismatch")})
    added: set[str] = set()
    by_id: dict[str, Any] = {}
    add_entities = MagicMock()

    button_module.async_add_task_entities(coordinator, added, by_id, add_entities)

    assert added == {PLAN_A}
    assert set(coordinator.data.map.plan) == {PLAN_A}
    add_entities.assert_called_once()
    assert len(add_entities.call_args.args[0]) == 1
    assert registry.removed == []


def test_remove_entities_looks_up_by_unique_name(
    button_module: types.ModuleType, registry: _Registry
) -> None:
    """Removal resolves the registry row with the same unique_id the entity registers."""
    coordinator = _coordinator({}, unique_name="Unique-1")
    registry.entities = {"button.a": _entry("button.a", f"Unique-1_{PLAN_A}")}

    button_module.async_remove_entities(coordinator, {PLAN_A})

    assert registry.lookups == [("button", "mammotion", f"Unique-1_{PLAN_A}")]
    assert registry.removed == ["button.a"]


def test_orphan_sweep_removes_only_stale_task_buttons(
    button_module: types.ModuleType, registry: _Registry
) -> None:
    """Only this device's button rows with a digit suffix and no plan are removed."""
    registry.entities = {
        e.entity_id: e
        for e in (
            _entry("button.stale", "Luba-TEST_17838509183764485777"),
            _entry("button.current", f"Luba-TEST_{PLAN_A}"),
            _entry("button.static", "Luba-TEST_start_map_sync"),
            _entry("button.other_device", "Luba-OTHER_17838509183764485777"),
            _entry("switch.area", "Luba-TEST_555", domain="switch"),
            _entry("sensor.task_area", "Luba-TEST_555_task_area", domain="sensor"),
        )
    }

    button_module.async_remove_orphaned_task_entities(
        _coordinator({PLAN_A: _plan(PLAN_A)})
    )

    assert registry.removed == ["button.stale"]


def test_orphan_sweep_skipped_without_data(
    button_module: types.ModuleType, registry: _Registry
) -> None:
    """Without coordinator data the plan list is unknown, so nothing is removed."""
    registry.entities = {
        "button.stale": _entry("button.stale", "Luba-TEST_17838509183764485777")
    }

    button_module.async_remove_orphaned_task_entities(_coordinator(None))

    assert registry.removed == []


def test_setup_entry_sweeps_orphans_before_first_sync(
    button_module: types.ModuleType, registry: _Registry
) -> None:
    """Platform setup removes cross-session orphans and adds the current plans."""
    registry.entities = {
        "button.stale": _entry("button.stale", "Luba-TEST_17838509183764485777")
    }
    mower = MagicMock()
    mower.device.device_name = "Luba-TEST"
    mower.reporting_coordinator = _coordinator({PLAN_A: _plan(PLAN_A)})
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    entry.runtime_data.spino = []
    add_entities = MagicMock()

    asyncio.new_event_loop().run_until_complete(
        button_module.async_setup_entry(MagicMock(), entry, add_entities)
    )

    assert registry.removed == ["button.stale"]
    task_entities = add_entities.call_args_list[0].args[0]
    assert [e.entity_description.plan_id for e in task_entities] == [PLAN_A]
