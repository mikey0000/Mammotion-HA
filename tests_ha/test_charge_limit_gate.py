"""Battery charge limit is offered only where the app offers it (issue #857).

The stubbed suite could only confirm the gate expressions appeared in the
source.  These run the real platform setups over a real ``MowingDevice`` and
read back the entities they actually create, so a gate that is wired to the
wrong firmware string fails here rather than passing on a token match.
"""

import json
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberMode
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings

from custom_components.mammotion import number as number_platform
from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.coordinator import (
    MammotionBaseUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_GATE_CASES = [
    ("Luba-VS123456", "2.1.1.5", True),
    ("Luba-VS123456", "2.3.28.1", True),
    ("Yuka-123456", "2.1.1.5", True),
    ("Luba-123456", "2.1.1.5", True),
    ("Luba-VS123456", "2.1.1.4", False),
    ("Luba-VS123456", "", False),
]


def _mower(name: str, firmware: str = "") -> MagicMock:
    """Wrap a real ``MowingDevice`` in the mower record the setups walk."""
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


async def _entities(platform: ModuleType, mower: MagicMock) -> dict[str, Any]:
    """Run the platform's own setup and return what it created, keyed by entity key."""
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    entry.runtime_data.spino = []
    entry.runtime_data.RTK = []
    add_entities = MagicMock()
    await platform.async_setup_entry(MagicMock(), entry, add_entities)
    return {
        entity.entity_description.key: entity
        for call in add_entities.call_args_list
        for entity in call[0][0]
    }


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_slider_exists_only_where_the_app_shows_the_page(
    name: str, firmware: str, expected: bool
) -> None:
    """The app hides Battery management below 2.1.1.5, and on an unread version."""
    entities = await _entities(number_platform, _mower(name, firmware))
    assert ("charge_limit" in entities) is expected


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_smart_charging_switch_follows_the_same_gate(
    name: str, firmware: str, expected: bool
) -> None:
    """Both controls belong to the one page, so one gate decides both."""
    entities = await _entities(switch_platform, _mower(name, firmware))
    assert ("smart_charge" in entities) is expected


async def test_the_gate_reads_the_firmware_the_device_reported() -> None:
    """``mower.device`` is the Aliyun binding record and carries no firmware."""
    mower = _mower("Luba-VS123456", "")
    mower.device.device_firmwares.device_version = "2.1.1.5"
    assert "charge_limit" not in await _entities(number_platform, mower)


async def test_the_slider_matches_the_app() -> None:
    """The app's charge-limit slider runs 80-100 in steps of 5."""
    entity = (await _entities(number_platform, _mower("Luba-VS123456", "2.1.1.5")))[
        "charge_limit"
    ]
    assert entity.native_min_value == 80
    assert entity.native_max_value == 100
    assert entity.native_step == 5
    assert entity.entity_description.mode is NumberMode.SLIDER


async def test_moving_the_slider_sets_a_whole_percent() -> None:
    """The protocol field is an int; HA hands the setter a float."""
    entity = (await _entities(number_platform, _mower("Luba-VS123456", "2.1.1.5")))[
        "charge_limit"
    ]
    entity.coordinator.async_set_charge_limit = AsyncMock()
    await entity.entity_description.set_async_fn(entity.coordinator, 85.0)
    entity.coordinator.async_set_charge_limit.assert_awaited_once_with(85)


@pytest.mark.parametrize(("reported", "expected"), [(0, None), (85, 85)])
async def test_an_unreported_limit_shows_as_unknown(
    reported: int, expected: int | None
) -> None:
    """0 is the proto default, not a limit the user could ever have set."""
    mower = _mower("Luba-VS123456", "2.1.1.5")
    mower.reporting_coordinator.data.mower_state.charge_settings.charge_limit = reported
    entity = (await _entities(number_platform, mower))["charge_limit"]
    assert entity.native_value == expected


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_startup_read_is_gated_the_same_way(
    name: str, firmware: str, expected: bool
) -> None:
    """The probe is not sent to devices the app would not show the page for."""
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    coordinator = AsyncMock()
    coordinator.device_name = name
    coordinator.data = device

    await MammotionReportUpdateCoordinator._async_startup_reads(coordinator)  # noqa: SLF001

    assert bool(coordinator.async_read_battery_info.await_count) is expected


async def test_setting_the_limit_resends_the_off_peak_window() -> None:
    """bms_ctrl_info_msg carries every setting, so the window must be echoed back."""
    device = MowingDevice()
    settings = device.mower_state.charge_settings
    settings.peak_valley_charge = True
    settings.valley_charge_start_time = 23
    settings.valley_charge_end_time = 6
    coordinator = AsyncMock()
    coordinator.data = device
    coordinator._async_set_battery_info = partial(  # noqa: SLF001
        MammotionBaseUpdateCoordinator._async_set_battery_info,  # noqa: SLF001
        coordinator,
    )

    await MammotionBaseUpdateCoordinator.async_set_charge_limit(coordinator, 90)

    coordinator.async_send_and_wait.assert_awaited_once_with(
        "set_battery_info",
        "bms_ctrl_info_msg",
        smart_charge=False,
        charge_limit=90,
        peak_valley_charge=True,
        valley_charge_start_time=23,
        valley_charge_end_time=6,
    )


def test_every_translation_names_the_new_entities() -> None:
    """Every locale and icons.json know the new number and switch keys."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    for path in files:
        data = json.loads(path.read_text())
        assert data["entity"]["number"]["charge_limit"]["name"], path
        assert data["entity"]["switch"]["smart_charge"]["name"], path
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    assert icons["number"]["charge_limit"]["default"].startswith("mdi:")
    assert icons["switch"]["smart_charge"]["default"].startswith("mdi:")
