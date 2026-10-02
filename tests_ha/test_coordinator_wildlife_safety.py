"""Wildlife Safety (WildGuard) goes out on ``nav_sys_param_cmd``, as the app sends it.

The app writes only the mode (id 12) and reads mode and status (ids 12, 13) on
``nav_sys_param_cmd`` on every device; it never writes id 13.  The setter and
reader run on the shipped coordinator around a spec'd client; the echo test
feeds a mower frame through a real handle and the library's reducer into the
select entity.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from area_rename_support import AreaRenameRig, make_area_rename_rig
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.mowing_modes import WildlifeSafety
from pymammotion.mammotion.commands.mammotion_command import MammotionCommand
from pymammotion.messaging.command_queue import Priority
from pymammotion.proto import (
    LubaMsg,
    MctlNav,
    MsgAttr,
    MsgCmdType,
    MsgDevice,
    NavSysParamMsg,
)
from pytest_homeassistant_custom_component.common import MockEntityPlatform
from user_command_support import make_coordinator

from custom_components.mammotion import select as select_platform
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_MOWER = "Luba-VS123456"


def _coordinator() -> MammotionReportUpdateCoordinator:
    return make_coordinator(
        MammotionReportUpdateCoordinator, MowingDevice(), device_name=_MOWER
    )


def _calls(
    coordinator: MammotionReportUpdateCoordinator,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (command, expected field, kwargs) for every command handed the client."""
    return [
        (call.args[1], call.args[2], call.kwargs)
        for call in coordinator.manager.send_command_and_wait.await_args_list
    ]


def _built(command: str, kwargs: dict[str, Any]) -> LubaMsg:
    """Build *command* the way the library will: a renamed method or kwarg fails here."""
    payload = {k: v for k, v in kwargs.items() if k not in ("prefer_ble", "priority")}
    return LubaMsg().parse(getattr(MammotionCommand(_MOWER, 1), command)(**payload))


@pytest.mark.regression
@pytest.mark.parametrize("mode", list(WildlifeSafety))
async def test_setting_a_mode_is_one_nav_write_of_that_mode(
    mode: WildlifeSafety,
) -> None:
    """The setter wrote id 13 on ``bidire_comm_cmd`` first and waited for a reply.

    The mower never answers that, so the command timed out and the mode (id 12)
    was never sent (Mammotion-HA#922).
    """
    coordinator = _coordinator()

    await coordinator.async_set_wildlife_safety(mode.value)

    [(command, expected, kwargs)] = _calls(coordinator)
    assert (command, expected) == ("set_animal_protection_mode", "nav_sys_param_cmd")
    assert kwargs["mode"] == mode.value
    assert kwargs["priority"] is Priority.USER
    param = _built(command, kwargs).nav.nav_sys_param_cmd
    assert (param.id, param.context, param.rw) == (12, mode.value, 1)


@pytest.mark.regression
async def test_reading_asks_for_the_mode_then_the_status_on_nav() -> None:
    """Both reads went out on ``bidire_comm_cmd`` and timed out unanswered."""
    coordinator = _coordinator()

    await coordinator.async_read_wildlife_safety()

    calls = _calls(coordinator)
    assert [(command, expected) for command, expected, _ in calls] == [
        ("read_animal_protection_mode", "nav_sys_param_cmd"),
        ("read_animal_protection_status", "nav_sys_param_cmd"),
    ]
    params = [
        _built(command, kwargs).nav.nav_sys_param_cmd for command, _, kwargs in calls
    ]
    assert [(p.id, p.rw) for p in params] == [(12, 0), (13, 0)]


def _mode_echo(mode: WildlifeSafety) -> bytes:
    """Return the mower's ``nav_sys_param_cmd`` id 12 answer carrying *mode*."""
    return bytes(
        LubaMsg(
            msgtype=MsgCmdType.NAV,
            sender=MsgDevice.DEV_MAINCTL,
            rcver=MsgDevice.DEV_MOBILEAPP,
            msgattr=MsgAttr.RESP,
            nav=MctlNav(
                nav_sys_param_cmd=NavSysParamMsg(id=12, context=mode.value, rw=1)
            ),
        )
    )


@pytest.fixture
async def rig(hass: HomeAssistant) -> AsyncIterator[AreaRenameRig]:
    """Load the select platform over a real handle; shut the coordinator down after."""
    rig = await make_area_rename_rig(hass, MowingDevice())
    platform = MockEntityPlatform(
        hass, domain="select", platform_name=DOMAIN, platform=select_platform
    )
    assert await platform.async_setup_entry(rig.entry)
    await hass.async_block_till_done()
    yield rig
    await rig.coordinator.async_shutdown()


async def test_the_devices_mode_answer_updates_the_select(
    hass: HomeAssistant, rig: AreaRenameRig
) -> None:
    """The reducer derives the status from the mode, so id 12 alone turns the select on."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "select", DOMAIN, f"{rig.coordinator.unique_name}_wildlife_safety"
    )
    assert entity_id, "wildlife safety select not registered"
    assert hass.states.get(entity_id).state == WildlifeSafety.off.name, (
        "premise: an unread device shows off"
    )

    await rig.handle.on_raw_message(
        _mode_echo(WildlifeSafety.low_speed_mowing),
        rig.handle.active_transport().transport_type,
    )
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == WildlifeSafety.low_speed_mowing.name
