"""Smart Sleep: the EcoSleep / LiveSleep switches and the wake-up button.

The app's battery page offers the two switches on the Luba 3 family above firmware
2.3.26.0.  Reading and writing them is the library's flow; the coordinator maps
its failures.  A sleeping mower has dropped its connection, so waking it goes
through the account's cloud login.
"""

from unittest.mock import call

import pytest
from aiohttp import ClientError
from battery_support import make_mower, platform_entities
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.data.model.device import MowingDevice
from pymammotion.http.model.http import UnauthorizedExceptionError
from user_command_support import make_coordinator, without_state_machine

from custom_components.mammotion import button as button_platform
from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.coordinator import (
    MammotionReportUpdateCoordinator,
    firmware_gated_reads,
)

_LUBA_3 = "Luba-VA123456"
_SUPPORTED = "2.3.26.1"

_OFFLINE = DeviceOfflineException(29003, "iot-1")

_GATE_CASES = [
    (_LUBA_3, _SUPPORTED, True),
    (_LUBA_3, "2.3.26.0", False),
    (_LUBA_3, "", False),
    ("Luba-VS123456", _SUPPORTED, False),
    ("Yuka-123456", _SUPPORTED, False),
]


def _coordinator() -> MammotionReportUpdateCoordinator:
    return make_coordinator(
        MammotionReportUpdateCoordinator, MowingDevice(), device_name=_LUBA_3
    )


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
async def test_the_switches_exist_only_where_the_app_shows_them(
    name: str, firmware: str, expected: bool
) -> None:
    """The gate is exclusive: 2.3.26.0 itself does not have the page."""
    entities = await platform_entities(switch_platform, make_mower(name, firmware))

    assert ("eco_sleep" in entities, "live_sleep" in entities) == (expected, expected)


@pytest.mark.parametrize(("name", "firmware", "expected"), _GATE_CASES)
def test_the_switches_are_read_under_the_same_gate(
    name: str, firmware: str, expected: bool
) -> None:
    """An entity nobody reads would show unknown all session."""
    assert (
        "async_read_smart_sleep" in firmware_gated_reads(name, firmware)
    ) is expected


async def test_a_switch_is_unknown_until_the_mower_has_been_read() -> None:
    """Showing "off" before the read would invite a write that changes nothing."""
    mower = make_mower(_LUBA_3, _SUPPORTED)
    entities = await platform_entities(switch_platform, mower)

    assert (entities["eco_sleep"].is_on, entities["live_sleep"].is_on) == (None, None)


async def test_each_switch_shows_its_own_setting() -> None:
    """EcoSleep is the docked setting and LiveSleep the off-dock one."""
    mower = make_mower(_LUBA_3, _SUPPORTED)
    mower.reporting_coordinator.data.mower_state.eco_sleep = True
    mower.reporting_coordinator.data.mower_state.live_sleep = False

    entities = await platform_entities(switch_platform, mower)

    assert (entities["eco_sleep"].is_on, entities["live_sleep"].is_on) == (True, False)


@pytest.mark.parametrize("method", ["set_eco_sleep", "set_live_sleep"])
async def test_a_write_goes_through_the_librarys_own_flow(method: str) -> None:
    """The library serialises the two switches and keeps the acked value if the re-read is lost."""
    coordinator = _coordinator()

    await getattr(coordinator, f"async_{method}")(True)

    getattr(coordinator.manager, method).assert_awaited_once_with(_LUBA_3, True)


async def test_the_startup_read_goes_through_the_librarys_own_flow() -> None:
    """The library serialises a read against a write in flight; a raw send would collide with it."""
    coordinator = _coordinator()

    await coordinator.async_read_smart_sleep()

    coordinator.manager.read_smart_sleep.assert_awaited_once_with(_LUBA_3)


async def test_the_wake_button_wakes_the_mower_through_the_cloud() -> None:
    """A sleeping mower has dropped its connection, so no device command would reach it."""
    coordinator = _coordinator()
    coordinator.manager.wake_device.return_value = True

    await coordinator.async_wake_up()

    coordinator.manager.wake_device.assert_awaited_once_with(_LUBA_3)


async def test_a_refused_wake_is_reported() -> None:
    """``wake_device`` answers False without raising when the cloud refuses or there is no login."""
    coordinator = _coordinator()
    coordinator.manager.wake_device.return_value = False

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_wake_up()

    assert raised.value.translation_key == "command_failed"


@pytest.mark.parametrize(
    "failure",
    [ClientError("connection reset"), UnauthorizedExceptionError("401")],
    ids=["network", "rejected login"],
)
async def test_a_wake_the_cloud_call_failed_is_reported_translated(
    failure: Exception,
) -> None:
    """The raw library exception would otherwise reach the user untranslated."""
    coordinator = _coordinator()
    coordinator.manager.wake_device.side_effect = failure

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_wake_up()

    assert raised.value.translation_key == "command_failed"
    assert raised.value.__cause__ is failure


@pytest.mark.parametrize("method", ["set_eco_sleep", "set_live_sleep"])
async def test_a_write_the_mower_never_got_is_reported(method: str) -> None:
    """A switch that stayed on after a dropped write would show a setting the mower does not have."""
    coordinator = _coordinator()
    getattr(coordinator.manager, method).side_effect = _OFFLINE

    with pytest.raises(HomeAssistantError) as raised:
        await getattr(coordinator, f"async_{method}")(True)

    assert raised.value.translation_key == "command_failed"


async def test_a_startup_read_the_mower_never_answered_is_not_an_error() -> None:
    """Nobody is waiting on it; raising would fail setup over a mower that is merely away."""
    coordinator = _coordinator()
    coordinator.manager.read_smart_sleep.side_effect = _OFFLINE

    await coordinator.async_read_smart_sleep()


@pytest.mark.parametrize(
    ("key", "written", "untouched"),
    [
        ("eco_sleep", "async_set_eco_sleep", "async_set_live_sleep"),
        ("live_sleep", "async_set_live_sleep", "async_set_eco_sleep"),
    ],
)
async def test_each_switch_writes_its_own_setting(
    hass: HomeAssistant, key: str, written: str, untouched: str
) -> None:
    """Crossed, the docked switch would change how the mower sleeps out on the lawn."""
    mower = make_mower(_LUBA_3, _SUPPORTED)
    entities = await platform_entities(switch_platform, mower)

    entity = without_state_machine(entities[key], hass)
    await entity.async_turn_on()
    await entity.async_turn_off()

    assert getattr(mower.reporting_coordinator, written).await_args_list == [
        call(True),
        call(False),
    ]
    getattr(mower.reporting_coordinator, untouched).assert_not_awaited()


async def test_pressing_the_wake_button_wakes_the_mower(hass: HomeAssistant) -> None:
    """The press is the only route to the wake call."""
    mower = make_mower(_LUBA_3, _SUPPORTED)
    mower.device.iot_id = "iot-1"
    mower.reporting_coordinator.hass = hass
    entities = await platform_entities(button_platform, mower, hass)

    await entities["wake_up"].async_press()

    mower.reporting_coordinator.async_wake_up.assert_awaited_once_with()


@pytest.mark.parametrize(("iot_id", "expected"), [("iot-1", True), ("", False)])
async def test_only_a_cloud_bound_mower_gets_the_wake_button(
    hass: HomeAssistant, iot_id: str, expected: bool
) -> None:
    """A BLE-only mower has no cloud login to send the wake through."""
    mower = make_mower(_LUBA_3, _SUPPORTED)
    mower.device.iot_id = iot_id
    mower.reporting_coordinator.hass = hass

    entities = await platform_entities(button_platform, mower, hass)

    assert ("wake_up" in entities) is expected
