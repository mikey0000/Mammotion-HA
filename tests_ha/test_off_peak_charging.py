"""Off-peak charging: the switch and the two ends of its window.

The app's battery page has an "Off-peak charge" row beside the charge limit, with
no gate of its own.  ``bms_ctrl_info_msg`` carries every battery setting, so each
of these writes resends what the mower reported for the others.
"""

from datetime import time
from typing import Any
from unittest.mock import call

import pytest
from battery_support import make_mower, platform_entities
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_info import ChargeSettings
from user_command_support import make_coordinator, without_state_machine

from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion import time as time_platform
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.models import MammotionMowerData

_MOWER = "Luba-VS123456"
_FIRMWARE = "2.3.30.39"
_REPORTED = ChargeSettings(
    smart_charge=True,
    charge_limit=100,
    peak_valley_charge=True,
    valley_charge_start_time=1320,
    valley_charge_end_time=360,
)


def _mower(settings: ChargeSettings | None = None) -> MammotionMowerData:
    mower = make_mower(_MOWER, _FIRMWARE)
    if settings is not None:
        mower.reporting_coordinator.data.mower_state.charge_settings = settings
    return mower


def _coordinator() -> MammotionReportUpdateCoordinator:
    device = MowingDevice()
    device.mower_state.charge_settings = _REPORTED
    return make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=_MOWER
    )


def _written(coordinator: MammotionReportUpdateCoordinator) -> dict[str, Any]:
    """Return the settings of the one battery write the coordinator handed the client."""
    (call,) = coordinator.manager.send_command_and_wait.await_args_list
    assert call.args[1] == "set_battery_info"
    return {
        key: call.kwargs[key]
        for key in (
            "smart_charge",
            "charge_limit",
            "peak_valley_charge",
            "valley_charge_start_time",
            "valley_charge_end_time",
        )
    }


async def test_the_window_shows_as_times_of_day() -> None:
    """The mower counts minutes since midnight: 1320 is 22:00 and 360 is 06:00."""
    entities = await platform_entities(time_platform, _mower(_REPORTED))

    assert entities["off_peak_charging_start"].native_value == time(22, 0)
    assert entities["off_peak_charging_end"].native_value == time(6, 0)


async def test_the_settings_are_unknown_until_the_mower_has_reported_them() -> None:
    """Midnight to midnight and "off" are real settings, not what an unread mower has."""
    mower = _mower()
    times = await platform_entities(time_platform, mower)
    switches = await platform_entities(switch_platform, mower)

    assert times["off_peak_charging_start"].native_value is None
    assert times["off_peak_charging_end"].native_value is None
    assert switches["off_peak_charging"].is_on is None


async def test_the_switch_shows_the_reported_setting() -> None:
    """It follows the mower's own switch, not smart charging."""
    switches = await platform_entities(switch_platform, _mower(_REPORTED))

    assert switches["off_peak_charging"].is_on is True


async def test_the_switch_writes_the_off_peak_setting(hass: HomeAssistant) -> None:
    """Crossed with smart charging, the press would change the charge limit instead."""
    mower = _mower(_REPORTED)
    entities = await platform_entities(switch_platform, mower)

    entity = without_state_machine(entities["off_peak_charging"], hass)
    await entity.async_turn_off()
    await entity.async_turn_on()

    coordinator = mower.reporting_coordinator
    assert coordinator.async_set_off_peak_charging.await_args_list == [
        call(False),
        call(True),
    ]
    coordinator.async_set_smart_charge.assert_not_awaited()


async def test_a_mower_without_the_battery_page_gets_no_window() -> None:
    """Firmware below 2.1.1.5 has no battery settings to write."""
    entities = await platform_entities(time_platform, make_mower(_MOWER, "2.0.0.0"))

    assert entities == {}


async def test_switching_it_off_keeps_the_limit_and_the_window() -> None:
    """Sending defaults for the rest would reset the limit and the window on the mower."""
    coordinator = _coordinator()

    await coordinator.async_set_off_peak_charging(False)

    assert _written(coordinator) == {
        "smart_charge": True,
        "charge_limit": 100,
        "peak_valley_charge": False,
        "valley_charge_start_time": 1320,
        "valley_charge_end_time": 360,
    }


@pytest.mark.parametrize(
    ("method", "changed"),
    [
        ("async_set_off_peak_start", "valley_charge_start_time"),
        ("async_set_off_peak_end", "valley_charge_end_time"),
    ],
)
async def test_setting_one_end_of_the_window_keeps_everything_else(
    method: str, changed: str
) -> None:
    """Only the end being set differs from what the mower reported."""
    coordinator = _coordinator()

    await getattr(coordinator, method)(1410)

    assert _written(coordinator) == {
        "smart_charge": True,
        "charge_limit": 100,
        "peak_valley_charge": True,
        "valley_charge_start_time": 1320,
        "valley_charge_end_time": 360,
        changed: 1410,
    }


@pytest.mark.parametrize(
    ("key", "method"),
    [
        ("off_peak_charging_start", "async_set_off_peak_start"),
        ("off_peak_charging_end", "async_set_off_peak_end"),
    ],
)
async def test_a_chosen_time_is_sent_as_minutes_since_midnight(
    key: str, method: str
) -> None:
    """23:30 is 1410 minutes, and each entity writes its own end of the window."""
    mower = _mower(_REPORTED)
    entities = await platform_entities(time_platform, mower)

    await entities[key].async_set_value(time(23, 30))

    getattr(mower.reporting_coordinator, method).assert_awaited_once_with(1410)
