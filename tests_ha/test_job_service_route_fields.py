"""start_mow and modify_running_job carry the route fields the switch and number set.

``auto_change_direction`` and ``ride_boundary_distance`` are planning settings a
user can also pass per call.  pymammotion's route builder gates both on the
model (and firmware / border laps), so the service layer only has to deliver
them; leaving them out must keep what the switch and number entities stored.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
import yaml
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.utility.constant.device_constant import WorkMode

from custom_components.mammotion import lawn_mower as lawn_mower_platform
from custom_components.mammotion.lawn_mower import (
    MODIFY_RUNNING_JOB_SCHEMA,
    START_MOW_SCHEMA,
    MammotionLawnMowerEntity,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_NEW_START_FIELDS = ("auto_change_direction", "ride_boundary_distance")


def _entity(settings: OperationSettings) -> MammotionLawnMowerEntity:
    """Build the real lawn mower entity over a docked mower with no breakpoint."""
    device = MowingDevice()
    device.report_data.dev.sys_status = WorkMode.MODE_READY
    device.report_data.dev.charge_state = 1
    coordinator = MagicMock()
    coordinator.unique_name = "Luba-VA123456"
    coordinator.device_name = "Luba-VA123456"
    coordinator.data = device
    coordinator.operation_settings = settings
    coordinator.async_ensure_fresh_state = AsyncMock()
    coordinator.async_request_report_snapshot = AsyncMock()
    coordinator.async_plan_route = AsyncMock(return_value=True)
    coordinator.async_send_and_wait = AsyncMock()
    coordinator.async_modify_running_job = AsyncMock(return_value=True)
    return MammotionLawnMowerEntity(coordinator)


async def _start_mow(
    entity: MammotionLawnMowerEntity, **data: Any
) -> OperationSettings:
    """Run start_mow through its own schema and return the settings it planned with."""
    await entity.async_start_mowing(**vol.Schema(START_MOW_SCHEMA)(data))
    return entity.coordinator.async_plan_route.await_args.args[0]


async def test_start_mow_plans_with_the_supplied_route_fields() -> None:
    """Both fields reach the route builder as given."""
    planned = await _start_mow(
        _entity(OperationSettings()),
        auto_change_direction=1,
        ride_boundary_distance=0.3,
    )

    assert planned.auto_change_direction == 1
    assert planned.ride_boundary_distance == 0.3


async def test_start_mow_without_the_fields_keeps_the_entity_settings() -> None:
    """No schema default may overwrite what the switch and number stored."""
    stored = OperationSettings(auto_change_direction=1, ride_boundary_distance=0.5)

    planned = await _start_mow(_entity(stored), speed=0.6)

    assert planned.auto_change_direction == 1
    assert planned.ride_boundary_distance == 0.5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auto_change_direction", 2),
        ("ride_boundary_distance", 1.5),
        ("ride_boundary_distance", -0.1),
    ],
)
def test_start_mow_rejects_out_of_range_values(field: str, value: float) -> None:
    """auto_change_direction is 0/1; the distance is a fraction from 0 to 1."""
    with pytest.raises(vol.Invalid):
        vol.Schema(START_MOW_SCHEMA)({field: value})


def test_start_mow_coerces_the_distance_to_float() -> None:
    """A YAML caller may send an int or a string."""
    validated = vol.Schema(START_MOW_SCHEMA)({"ride_boundary_distance": "1"})
    assert validated["ride_boundary_distance"] == 1.0
    assert isinstance(validated["ride_boundary_distance"], float)


async def test_every_registered_entity_service_exists_on_the_entity() -> None:
    """HA resolves ``func`` by name on the entity; a missing one fails every call."""
    register = MagicMock()
    entry = MagicMock()
    entry.runtime_data.mowers = []
    with patch.object(
        lawn_mower_platform.service,
        "async_register_platform_entity_service",
        register,
    ):
        await lawn_mower_platform.async_setup_entry(MagicMock(), entry, MagicMock())

    funcs = [call.kwargs["func"] for call in register.call_args_list]
    assert "async_modify_running_job" in funcs
    assert [f for f in funcs if not hasattr(MammotionLawnMowerEntity, f)] == []


async def test_modify_running_job_forwards_the_fields_to_the_coordinator() -> None:
    """Every field the schema accepts, auto_change_direction included, is handed on."""
    entity = _entity(OperationSettings())
    data = vol.Schema(MODIFY_RUNNING_JOB_SCHEMA)(
        {"auto_change_direction": "1", "speed": 0.8}
    )

    await entity.async_modify_running_job(**data)

    entity.coordinator.async_modify_running_job.assert_awaited_once_with(
        auto_change_direction=1, speed=0.8
    )


def test_services_yaml_declares_the_start_mow_fields() -> None:
    """Optional, with no default, and bounded like the modify and task fields."""
    fields = yaml.safe_load((_ROOT / "services.yaml").read_text())["start_mow"][
        "fields"
    ]
    for name in _NEW_START_FIELDS:
        assert fields[name]["required"] is False, name
        assert "default" not in fields[name], name
    assert fields["auto_change_direction"]["selector"]["number"] == {
        "min": 0,
        "max": 1,
        "step": 1,
    }
    distance = fields["ride_boundary_distance"]["selector"]["number"]
    assert (distance["min"], distance["max"], distance["step"]) == (0, 1, 0.1)


def test_the_start_mow_fields_are_translated_in_every_locale() -> None:
    """strings.json plus every locale, in each locale's own language."""
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 2
    for path in files:
        fields = json.loads(path.read_text(encoding="utf-8"))["services"]["start_mow"][
            "fields"
        ]
        for name in _NEW_START_FIELDS:
            field = fields[name]
            assert field["name"] and field["description"], (path.name, name)
            if path.stem not in ("en", "strings"):
                en_field = english["services"]["start_mow"]["fields"][name]
                assert field["name"] != en_field["name"], (path.name, name)
                assert field["description"] != en_field["description"], (
                    path.name,
                    name,
                )
