"""Wildlife Safety is offered only where the app offers it (issue #853).

The stubbed suite could only confirm the gate expressions appeared in the
source.  Here the select platform really runs and the startup read list is
really built, so a gate wired to the wrong firmware string fails.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings

from custom_components.mammotion import select as select_platform
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

# The app hides the row on Luba 1, the 231-family parameter set (Yuka MV, Ezy VT,
# Luba MB) and below firmware 1.13; an unread version keeps it, as the app does.
_GATE_CASES = [
    ("Luba-VS123456", "1.13", True),
    ("Luba-VS123456", "", True),
    ("Yuka-123456", "2.0.0", True),
    ("Luba-VS123456", "1.12", False),
    ("Luba-123456", "2.0.0", False),
    ("Yuka-MV123456", "2.0.0", False),
    ("Luba-MB123456", "2.0.0", False),
]


def _mower(name: str, firmware: str = "") -> MagicMock:
    """Wrap a real ``MowingDevice`` in the mower record the setup walks."""
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    mower = MagicMock()
    mower.name = name
    mower.device.device_name = name
    mower.device.product_key = ""
    mower.api.get_device_by_name.return_value = None
    coordinator = mower.reporting_coordinator
    coordinator.data = device
    coordinator.device_name = name
    coordinator.unique_name = name
    coordinator.operation_settings = OperationSettings()
    return mower


async def _entities(mower: MagicMock) -> dict[str, Any]:
    """Run the select platform's own setup and return what it created."""
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    entry.runtime_data.spino = []
    add_entities = MagicMock()
    await select_platform.async_setup_entry(MagicMock(), entry, add_entities)
    return {
        entity.entity_description.key: entity
        for call in add_entities.call_args_list
        for entity in call[0][0]
    }


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_select_exists_only_where_the_app_shows_the_row(
    name: str, firmware: str, expected: bool
) -> None:
    """Mirrors CarSettingDrawerFragment.setAnimalProtectVisible."""
    entities = await _entities(_mower(name, firmware))
    assert ("wildlife_safety" in entities) is expected


async def test_the_gate_reads_the_firmware_the_device_reported() -> None:
    """``mower.device`` is the Aliyun binding record and carries no firmware."""
    mower = _mower("Luba-VS123456", "1.12")
    mower.device.device_firmwares.device_version = "1.13"
    assert "wildlife_safety" not in await _entities(mower)


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_startup_read_is_gated_the_same_way(
    name: str, firmware: str, expected: bool
) -> None:
    """The probe is not sent to devices the app would not show the row for."""
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    coordinator = AsyncMock()
    coordinator.device_name = name
    coordinator.data = device

    await MammotionReportUpdateCoordinator._async_startup_reads(coordinator)  # noqa: SLF001

    assert bool(coordinator.async_read_wildlife_safety.await_count) is expected
