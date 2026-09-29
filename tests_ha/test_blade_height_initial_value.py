"""The blade height number starts at the mower's own blade height, not 0.

The entity plans the next job, so its value lives on ``operation_settings``,
whose default is 0.  Until something sets it, the device's reported height
(``report_data.work.knife_height``, 0 until the first report) is adopted, so a
freshly added mower shows and plans what it is actually cutting at.  A restored
or user-chosen planning value is kept.  The number platform really runs here and
user values arrive through ``async_set_native_value``.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberExtraStoredData
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings

from custom_components.mammotion import number as number_platform
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator
from custom_components.mammotion.entity import MammotionBaseEntity

_KEY = "blade_height"
_LUBA = "Luba-VAME9R5S"
_YUKA = "Yuka-MV123456"


def _mower(name: str = _LUBA, knife_height: int = 0) -> MagicMock:
    """Wrap a real ``MowingDevice`` in the mower record the setup walks."""
    device = MowingDevice()
    device.report_data.work.knife_height = knife_height
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
    coordinator.async_change_blade_height_if_working = AsyncMock()
    return mower


async def _entities(mower: MagicMock) -> dict[str, Any]:
    """Run the number platform's own setup and return what it created, by key."""
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    entry.runtime_data.spino = []
    add_entities = MagicMock()
    await number_platform.async_setup_entry(MagicMock(), entry, add_entities)
    return {
        entity.entity_description.key: entity
        for call in add_entities.call_args_list
        for entity in call[0][0]
    }


async def _added(
    monkeypatch: pytest.MonkeyPatch,
    knife_height: int = 0,
    restored: float | None = None,
) -> Any:
    """Build the blade height entity and add it, restoring ``restored`` if given."""
    # The base entity's hook reaches the device registry, which needs a hass.
    monkeypatch.setattr(MammotionBaseEntity, "async_added_to_hass", AsyncMock())
    entity = (await _entities(_mower(knife_height=knife_height)))[_KEY]
    entity.async_write_ha_state = MagicMock()
    last = (
        None if restored is None else NumberExtraStoredData(70, 30, 1, "mm", restored)
    )
    entity.async_get_last_number_data = AsyncMock(return_value=last)
    await entity.async_added_to_hass()
    return entity


def _report(entity: Any, knife_height: int) -> None:
    """Deliver a work report carrying ``knife_height`` to the entity."""
    entity.coordinator.data.report_data.work.knife_height = knife_height
    entity._handle_coordinator_update()


async def test_a_new_mower_starts_at_its_reported_height(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh add with the height already reported shows and plans it, not 0."""
    entity = await _added(monkeypatch, knife_height=55)

    assert entity.native_value == 55
    assert entity.coordinator.operation_settings.blade_height == 55


async def test_the_first_report_after_setup_seeds_the_height(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup runs seconds before the first report; the report then seeds it."""
    entity = await _added(monkeypatch)

    assert entity.native_value is None, "unknown, not a made-up 0"
    _report(entity, 55)

    assert entity.native_value == 55
    assert entity.coordinator.operation_settings.blade_height == 55


async def test_a_later_report_does_not_move_the_planning_height(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the first known height seeds it; the mower's later changes do not."""
    entity = await _added(monkeypatch, knife_height=55)

    _report(entity, 40)

    assert entity.native_value == 55
    assert entity.coordinator.operation_settings.blade_height == 55


async def test_a_restored_value_wins_over_the_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a restart the user's planning height is kept, not the mower's."""
    entity = await _added(monkeypatch, knife_height=55, restored=45)
    _report(entity, 55)

    assert entity.native_value == 45
    assert entity.coordinator.operation_settings.blade_height == 45


async def test_a_restored_zero_is_replaced_by_the_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored 0 is the old unseeded value, never a height the user chose."""
    entity = await _added(monkeypatch, restored=0)
    _report(entity, 55)

    assert entity.native_value == 55
    assert entity.coordinator.operation_settings.blade_height == 55


async def test_a_user_value_set_before_the_first_report_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A value picked before the mower reports is not overwritten by the report."""
    entity = await _added(monkeypatch)

    # HA's service wrapper needs a platform for this entity's mm unit.
    await entity.async_set_native_value(45)
    _report(entity, 55)

    assert entity.native_value == 45
    assert entity.coordinator.operation_settings.blade_height == 45


async def test_yuka_has_no_blade_height_number_and_plans_minus_ten() -> None:
    """Yuka's height is fixed by the route, whatever the device reports."""
    mower = _mower(_YUKA, knife_height=55)

    assert _KEY not in await _entities(mower)
    route = MammotionBaseUpdateCoordinator.generate_route_information(
        mower.reporting_coordinator, mower.reporting_coordinator.operation_settings
    )
    assert route.blade_height == -10
