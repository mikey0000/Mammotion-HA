"""A mower without a cloud identity (BLE-only) gets a Bluetooth switch but no cloud switch."""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

_switch = sys.modules["custom_components.mammotion.switch"]


@pytest.fixture
def platform(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Switch module with entity classes mocked and every device-type check false."""
    for name in (
        "MammotionSwitchEntity",
        "MammotionConfigSwitchEntity",
        "MammotionUpdateSwitchEntity",
        "MammotionSpinoSwitchEntity",
    ):
        monkeypatch.setattr(_switch, name, MagicMock())
    device_type = MagicMock()
    for check in ("is_luba_pro", "is_yuka", "is_yuka_mini", "is_luba1", "is_mini_or_x_series"):
        getattr(device_type, check).return_value = False
    monkeypatch.setattr(_switch, "DeviceType", device_type)
    return _switch


def _mower(name: str, iot_id: str) -> MagicMock:
    mower = MagicMock()
    mower.device.device_name = name
    mower.device.iot_id = iot_id
    # No map data, so the area switch helper returns before touching the coordinator.
    mower.reporting_coordinator.data = None
    return mower


def _switch_keys(platform: Any) -> set[str]:
    return {
        call.args[1].key
        for call in platform.MammotionSwitchEntity.call_args_list
    }


def _setup(platform: Any, *mowers: MagicMock) -> None:
    entry = MagicMock()
    entry.runtime_data.mowers = list(mowers)
    entry.runtime_data.spino = []
    asyncio.new_event_loop().run_until_complete(
        platform.async_setup_entry(MagicMock(), entry, MagicMock())
    )


def test_ble_only_mower_has_bluetooth_switch_but_no_cloud_switch(platform: Any) -> None:
    _setup(platform, _mower("Luba-BLE", ""))

    keys = _switch_keys(platform)
    assert "bluetooth_enabled" in keys
    assert "cloud_enabled" not in keys


def test_cloud_mower_has_both_connectivity_switches(platform: Any) -> None:
    _setup(platform, _mower("Luba-Cloud", "iot-123"))

    keys = _switch_keys(platform)
    assert {"bluetooth_enabled", "cloud_enabled"} <= keys
