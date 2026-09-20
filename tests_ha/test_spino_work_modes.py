"""Spino cleaning modes and the force module are per model, not per enum.

A user reported an E1 offering Waterline and Custom, which that hardware does
not have: the app carries a separate mode enum for the PC210 SP and gates
Waterline on the PC200.  The force/turbo toggle is gated the other way — the app
hides it on the S1 and the SP (``SwimmingPoolTestToolsActivity:251-256``).

The stubbed suite could only confirm that ``for_device`` appeared in each
platform.  Here every platform really runs and the option lists it hands Home
Assistant are the ones asserted on.
"""

from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest
from pymammotion.data.model.device import PoolCleanerDevice
from pymammotion.utility.device_type import DeviceType

from custom_components.mammotion import select as select_platform
from custom_components.mammotion import sensor as sensor_platform
from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion import vacuum as vacuum_platform

_E1 = "Spino-E1C36JT4"
_PLAIN = "Spino-123456"
# A ``Spino-S1`` name is a PC210, i.e. SWIMMINGPOOL_SP — the APK says so outright.
_SP = "Spino-SP123456"
_S1_NAME = "Spino-S1123456"

_FOUR_MODES = ["AUTO", "FLOOR", "WALL", "ECO"]
_SIX_MODES = ["AUTO", "FLOOR", "WALL", "ECO", "LINE", "CUSTOM"]


def _spino(name: str) -> MagicMock:
    """Wrap a real ``PoolCleanerDevice`` in the cleaner record the setups walk."""
    spino = MagicMock()
    coordinator = spino.coordinator
    coordinator.data = PoolCleanerDevice()
    coordinator.device_name = name
    coordinator.unique_name = name
    coordinator.device.product_key = ""
    coordinator.device.product_model = ""
    coordinator.device_type = DeviceType.value_of_str(name, "")
    return spino


async def _entities(platform: ModuleType, name: str) -> dict[str, Any]:
    """Run the platform's own setup for one cleaner and return what it created."""
    entry = MagicMock()
    entry.runtime_data.mowers = []
    entry.runtime_data.RTK = []
    entry.runtime_data.spino = [_spino(name)]
    add_entities = MagicMock()
    await platform.async_setup_entry(MagicMock(), entry, add_entities)
    return {
        entity.entity_description.key: entity
        for call in add_entities.call_args_list
        for entity in call[0][0]
    }


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (_E1, _FOUR_MODES),
        (_PLAIN, _FOUR_MODES),
        (_SP, _SIX_MODES),
        (_S1_NAME, _SIX_MODES),
    ],
)
async def test_the_vacuum_fan_speed_list_is_per_device(
    name: str, expected: list[str]
) -> None:
    """It was a module constant, so every cleaner advertised every mode."""
    entry = MagicMock()
    entry.runtime_data.spino = [_spino(name)]
    add_entities = MagicMock()
    await vacuum_platform.async_setup_entry(MagicMock(), entry, add_entities)
    (vacuum,) = add_entities.call_args[0][0]
    assert vacuum.fan_speed_list == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (_E1, _FOUR_MODES),
        (_PLAIN, _FOUR_MODES),
        (_SP, _SIX_MODES),
        (_S1_NAME, _SIX_MODES),
    ],
)
async def test_the_work_mode_select_is_built_per_device(
    name: str, expected: list[str]
) -> None:
    """Which modes exist is a property of the model, not of the enum."""
    entities = await _entities(select_platform, name)
    assert entities["spino_work_mode"].entity_description.options == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [(_E1, _FOUR_MODES), (_SP, _SIX_MODES)],
)
async def test_the_work_mode_sensor_also_reports_the_two_non_modes(
    name: str, expected: list[str]
) -> None:
    """The sensor reports rather than commands, so OFF/UNKNOWN belong in it."""
    entities = await _entities(sensor_platform, name)
    assert entities["spino_work_mode"].entity_description.options == [
        "UNKNOWN",
        "OFF",
        *expected,
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [(_E1, True), (_PLAIN, True), (_SP, False), (_S1_NAME, False)],
)
async def test_the_turbo_toggle_is_hidden_on_the_s1_and_sp(
    name: str, expected: bool
) -> None:
    """The app hides that row for both; every other toggle is unconditional."""
    entities = await _entities(switch_platform, name)
    assert ("spino_turbo_clean" in entities) is expected
    assert "spino_buzzer" in entities


async def test_no_spino_platform_offers_a_mode_the_e1_lacks() -> None:
    """The regression: `for mode in SpinoWorkMode` ignored the model entirely."""
    entry = MagicMock()
    entry.runtime_data.mowers = []
    entry.runtime_data.RTK = []
    entry.runtime_data.spino = [_spino(_E1)]
    add_entities = MagicMock()
    await vacuum_platform.async_setup_entry(MagicMock(), entry, add_entities)
    (vacuum,) = add_entities.call_args[0][0]

    offered = {
        *vacuum.fan_speed_list,
        *(await _entities(select_platform, _E1))[
            "spino_work_mode"
        ].entity_description.options,
        *(await _entities(sensor_platform, _E1))[
            "spino_work_mode"
        ].entity_description.options,
    }
    assert offered.isdisjoint({"LINE", "CUSTOM", "RECHARGE"})
