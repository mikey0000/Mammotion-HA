"""The fill-light switches show what the mower holds after a press.

The mower's ``set_lamp_rsp`` carries only the id and a result, so nothing wrote
the new state into ``lamp_info`` and the switch fell back on the next push.  The
client here is a spec'd stand-in for a mower that keeps its lamps and answers a
``get_car_light`` with them; the coordinator and the switch are the shipped ones.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.data.model.device import MowingDevice
from user_command_support import make_coordinator, without_state_machine

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.switch import (
    FILL_LIGHT_CONFIG_SWITCH_ENTITIES,
    MammotionSwitchEntity,
)

_MOWER = "Yuka-MV123456"

# get_car_light ids -> the lamp_info field its reply fills.
_READ_IDS = {1126: "manual_light", 1123: "night_light"}
_SET_KWARGS = {
    "set_car_manual_light": ("manual_light", "manual_ctrl"),
    "set_car_light": ("night_light", "on_off"),
}


def _switch(
    hass: HomeAssistant, key: str, *, initially_on: bool
) -> tuple[MammotionSwitchEntity, MowingDevice]:
    """Return the shipped *key* switch over a mower that answers light reads."""
    device = MowingDevice()
    setattr(device.mower_state.lamp_info, key, initially_on)
    held = {"manual_light": initially_on, "night_light": initially_on}
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=_MOWER
    )
    coordinator.unique_name = _MOWER

    async def _mower(
        _name: str, command: str, _expected_field: str, **kwargs: Any
    ) -> None:
        if command == "get_car_light":
            field = _READ_IDS[kwargs["ids"]]
            setattr(device.mower_state.lamp_info, field, held[field])
            return
        field, kwarg = _SET_KWARGS[command]
        held[field] = kwargs[kwarg]

    coordinator.manager.send_command_and_wait.side_effect = _mower
    description = next(d for d in FILL_LIGHT_CONFIG_SWITCH_ENTITIES if d.key == key)
    switch = without_state_machine(
        MammotionSwitchEntity(coordinator, description), hass
    )
    return switch, device


_TURN_ON = MammotionSwitchEntity.async_turn_on
_TURN_OFF = MammotionSwitchEntity.async_turn_off


@pytest.mark.regression
@pytest.mark.parametrize(
    ("key", "press", "expected"),
    [
        pytest.param("manual_light", _TURN_ON, True, id="manual_light_on"),
        pytest.param("manual_light", _TURN_OFF, False, id="manual_light_off"),
        pytest.param("night_light", _TURN_ON, True, id="night_light_on"),
        pytest.param("night_light", _TURN_OFF, False, id="night_light_off"),
    ],
)
async def test_light_switch_keeps_its_state_after_the_next_update(
    hass: HomeAssistant,
    key: str,
    press: Callable[[MammotionSwitchEntity], Awaitable[None]],
    expected: bool,
) -> None:
    """The switch fell back a few seconds after a press while the light stayed lit (#964).

    Nothing read the lamp back after the set, so the first coordinator update
    after the press re-read the stale ``lamp_info`` and undid it; a dashboard
    toggle then sent ``turn_on`` again instead of ``turn_off``.
    """
    switch, _ = _switch(hass, key, initially_on=not expected)

    await press(switch)
    switch._handle_coordinator_update()  # noqa: SLF001

    assert switch.is_on is expected


@pytest.mark.parametrize("key", ["manual_light", "night_light"])
async def test_a_lost_read_back_does_not_fail_the_press(
    hass: HomeAssistant, key: str
) -> None:
    """The light did change; only the confirmation was lost."""
    switch, _ = _switch(hass, key, initially_on=False)
    mower = switch.coordinator.manager.send_command_and_wait.side_effect

    async def _reads_lost(
        name: str, command: str, expected_field: str, **kwargs: Any
    ) -> None:
        if command == "get_car_light":
            raise DeviceOfflineException(29003, "iot-1")
        await mower(name, command, expected_field, **kwargs)

    switch.coordinator.manager.send_command_and_wait.side_effect = _reads_lost

    await switch.async_turn_on()

    assert switch.is_on is True
