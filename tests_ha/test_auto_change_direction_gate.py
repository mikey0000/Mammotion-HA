"""Auto-reverse mowing direction is offered only where the app offers it (issue #860).

The stubbed suite could only confirm the gate expressions appeared in the
source.  Here the switch platform really runs, and the planned route really
comes out of ``generate_route_information``.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings

from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

# Mammotion's release notes list the toggle for these models only, from 2.3.28.1.
_GATE_CASES = [
    ("Luba-VA123456", "2.3.28.1", True),
    ("Luba-MB123456", "2.4.0.0", True),
    ("Yuka-MV123456", "2.3.28.1", True),
    ("Luba-VA123456", "2.3.28.0", False),
    ("Luba-VA123456", "", False),
    ("Luba-VS123456", "2.3.28.1", False),
    ("Yuka-123456", "2.3.28.1", False),
]


def _mower(name: str, firmware: str = "") -> MagicMock:
    """Wrap a real ``MowingDevice`` in the mower record the setup walks."""
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    mower = MagicMock()
    mower.name = name
    mower.device.device_name = name
    mower.device.product_key = ""
    mower.device.iot_id = "iot-id"
    mower.api.get_device_by_name.return_value = None
    coordinator = mower.reporting_coordinator
    coordinator.data = device
    coordinator.device_name = name
    coordinator.unique_name = name
    coordinator.operation_settings = OperationSettings()
    return mower


async def _entities(mower: MagicMock) -> dict[str, Any]:
    """Run the switch platform's own setup and return what it created."""
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    entry.runtime_data.spino = []
    add_entities = MagicMock()
    await switch_platform.async_setup_entry(MagicMock(), entry, add_entities)
    return {
        entity.entity_description.key: entity
        for call in add_entities.call_args_list
        for entity in call[0][0]
    }


def _coordinator(name: str, firmware: str) -> AsyncMock:
    """Build a stand-in carrying real report data, for the unbound coordinator calls."""
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    coordinator = AsyncMock()
    coordinator.device_name = name
    coordinator.data = device
    return coordinator


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_toggle_exists_only_where_the_app_shows_the_row(
    name: str, firmware: str, expected: bool
) -> None:
    """An unread firmware version counts as unsupported: the toggle writes state."""
    entities = await _entities(_mower(name, firmware))
    assert ("auto_change_direction" in entities) is expected


async def test_the_gate_reads_the_firmware_the_device_reported() -> None:
    """``mower.device`` is the Aliyun binding record and carries no firmware."""
    mower = _mower("Luba-VA123456", "")
    mower.device.device_firmwares.device_version = "2.3.28.1"
    assert "auto_change_direction" not in await _entities(mower)


@pytest.mark.parametrize(("value", "stored"), [(True, 1), (False, 0)])
async def test_the_switch_writes_the_operation_setting(
    value: bool, stored: int
) -> None:
    """Toggling stores 0/1 on operation_settings so the next plan carries it."""
    entity = (await _entities(_mower("Luba-VA123456", "2.3.28.1")))[
        "auto_change_direction"
    ]
    entity.entity_description.set_fn(entity.coordinator, value)
    assert entity.coordinator.operation_settings.auto_change_direction == stored


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
def test_route_generation_gates_the_setting_on_device_support(
    name: str, firmware: str, expected: bool
) -> None:
    """The send path gates too, not just entity setup.

    operation_settings survive a device swap and the coordinator plans routes from
    them, so an entity-level gate alone would still write the setting.
    """
    settings = OperationSettings()
    settings.auto_change_direction = 1

    route = MammotionBaseUpdateCoordinator.generate_route_information(
        _coordinator(name, firmware), settings
    )

    assert route.auto_change_direction == (1 if expected else 0)


def test_every_translation_names_the_new_switch() -> None:
    """Every locale and icons.json know the new switch key, each in its own language."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    names = {}
    for path in files:
        data = json.loads(path.read_text())
        name = data["entity"]["switch"]["auto_change_direction"]["name"]
        assert name, path
        names[path.name] = name
    # Only strings.json and en.json may share the English wording.
    english = names["strings.json"]
    assert [f for f, n in names.items() if n == english] == ["strings.json", "en.json"]
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    assert icons["switch"]["auto_change_direction"]["default"].startswith("mdi:")
