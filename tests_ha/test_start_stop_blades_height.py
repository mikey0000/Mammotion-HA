"""start_stop_blades without a height uses the Blade height entity's value.

The schema defaulted an omitted height to 30, services.yaml advertised 25 and
the coordinator's own default is 60; none of them was the height the user had
set on the Blade height entity.  The order the height is resolved in lives on
the coordinator, which the service and the Blade height number both read.
"""

from pathlib import Path
from typing import Any

import pytest
import voluptuous as vol
import yaml
from pymammotion.data.model.device_config import OperationSettings
from user_command_support import make_cloud_report_coordinator

from custom_components.mammotion.coordinator import (
    BLADE_HEIGHT_FALLBACK_MM,
    MammotionReportUpdateCoordinator,
)
from custom_components.mammotion.lawn_mower import (
    START_STOP_BLADES_SCHEMA,
    MammotionLawnMowerEntity,
)

_DEVICE_NAME = "Luba-VAME9R5S"
_SERVICES_YAML = (
    Path(__file__).parent.parent / "custom_components" / "mammotion" / "services.yaml"
)


def _coordinator(
    *, entity_height: int = 0, device_height: int = 0
) -> MammotionReportUpdateCoordinator:
    coordinator = make_cloud_report_coordinator(
        _DEVICE_NAME, operation_settings=OperationSettings(blade_height=entity_height)
    )
    coordinator.data.report_data.work.knife_height = device_height
    return coordinator


async def _call(coordinator: MammotionReportUpdateCoordinator, **data: Any) -> int:
    """Run the service through its schema and the entity; return the height sent."""
    entity = MammotionLawnMowerEntity(coordinator)
    await entity.async_start_stop_blades(**vol.Schema(START_STOP_BLADES_SCHEMA)(data))
    return coordinator.manager.send_command_with_args.await_args.kwargs[
        "cut_knife_height"
    ]


@pytest.mark.regression
def test_the_schema_fills_in_no_height() -> None:
    """An omitted height must reach the entity as omitted."""
    assert "blade_height" not in vol.Schema(START_STOP_BLADES_SCHEMA)({})


@pytest.mark.regression
async def test_an_omitted_height_is_the_blade_height_entitys() -> None:
    """The entity's stored height, not a hard-coded one."""
    assert await _call(_coordinator(entity_height=45), start_stop=True) == 45


async def test_before_the_entity_has_a_value_the_mowers_height_is_used() -> None:
    """The entity adopts the reported height, so the call does too."""
    coordinator = _coordinator(device_height=55)

    assert await _call(coordinator, start_stop=True) == 55


async def test_a_given_height_wins() -> None:
    """The caller's height is sent as given."""
    coordinator = _coordinator(entity_height=45, device_height=55)

    assert await _call(coordinator, start_stop=False, blade_height=70) == 70


@pytest.mark.parametrize(
    ("entity_height", "device_height", "expected"),
    [
        pytest.param(45, 55, 45, id="planned"),
        pytest.param(0, 55, 55, id="reported"),
        pytest.param(0, 0, None, id="unknown"),
    ],
)
def test_the_blade_height_is_the_planned_one_then_the_reported_one(
    entity_height: int, device_height: int, expected: int | None
) -> None:
    """0 is the unset default on both sides, never a real height."""
    coordinator = _coordinator(entity_height=entity_height, device_height=device_height)

    assert coordinator.blade_height == expected


async def test_with_no_height_known_the_fallback_is_sent() -> None:
    """Neither entity nor mower has a height yet; the blade still needs one."""
    assert await _call(_coordinator(), start_stop=True) == BLADE_HEIGHT_FALLBACK_MM


def test_services_yaml_shows_no_height_default() -> None:
    """The UI must not prefill a height the entity does not hold."""
    fields = yaml.safe_load(_SERVICES_YAML.read_text())["start_stop_blades"]["fields"]
    assert "default" not in fields["blade_height"]
