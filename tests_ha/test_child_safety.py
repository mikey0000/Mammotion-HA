"""Child safety: the switch the app shows on mowers whose function list has it.

The app asks the server whether function code 003.001 is listed for the mower's
current firmware.  That list is fetched after setup, so the switch and its read
have to appear on a later coordinator update.  Reading and writing are the
library's flow; the coordinator maps its failures.
"""

from unittest.mock import call

import pytest
from battery_support import make_mower, make_report_coordinator, platform_entities
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pymammotion.aliyun.exceptions import DeviceOfflineException
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.function_codes import FunctionCodes
from pymammotion.state.device_state import DeviceStateMachine
from user_command_support import make_coordinator, without_state_machine

from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.coordinator import (
    MammotionReportUpdateCoordinator,
    firmware_gated_reads,
)
from custom_components.mammotion.models import MammotionMowerData

_LUBA_3 = "Luba-VA123456"
_FIRMWARE = "2.3.30.39"
_LISTED = FunctionCodes(product_version=_FIRMWARE, codes=["003.001"])


def _mower(
    function_codes: FunctionCodes | None = None, name: str = _LUBA_3
) -> MammotionMowerData:
    mower = make_mower(name, _FIRMWARE)
    if function_codes is not None:
        mower.reporting_coordinator.data.function_codes = function_codes
    return mower


def _coordinator() -> MammotionReportUpdateCoordinator:
    return make_coordinator(
        MammotionReportUpdateCoordinator, MowingDevice(), device_name=_LUBA_3
    )


async def test_a_mower_whose_function_list_lacks_it_has_no_switch() -> None:
    """The app hides the row, so the mower may not know the setting at all."""
    entities = await platform_entities(switch_platform, _mower())

    assert "child_safety" not in entities


async def test_a_mower_whose_function_list_has_it_gets_the_switch() -> None:
    """Unknown until read: "off" would invite a write that changes nothing."""
    entities = await platform_entities(switch_platform, _mower(_LISTED))

    assert entities["child_safety"].is_on is None


async def test_a_mower_with_no_command_for_it_gets_no_switch() -> None:
    """Only X3 and X5 mowers carry the setting; the library refuses to send it to a Luba 1."""
    entities = await platform_entities(
        switch_platform, _mower(_LISTED, name="Luba-ABCDEF")
    )

    assert "child_safety" not in entities


async def test_the_switch_writes_child_safety(hass: HomeAssistant) -> None:
    """The press is the only route to the write."""
    mower = _mower(_LISTED)
    entities = await platform_entities(switch_platform, mower)

    entity = without_state_machine(entities["child_safety"], hass)
    await entity.async_turn_on()
    await entity.async_turn_off()

    assert mower.reporting_coordinator.async_set_child_safety.await_args_list == [
        call(True),
        call(False),
    ]


async def test_the_switch_shows_what_the_mower_reported() -> None:
    """The reducer stores the read-back as a bool on the mower state."""
    mower = _mower(_LISTED)
    mower.reporting_coordinator.data.mower_state.child_safety = True

    entities = await platform_entities(switch_platform, mower)

    assert entities["child_safety"].is_on is True


def test_it_is_read_only_once_the_function_list_has_it() -> None:
    """An entity nobody reads would show unknown all session."""
    assert "async_read_child_safety" in firmware_gated_reads(
        _LUBA_3, _FIRMWARE, child_safety=True
    )
    assert "async_read_child_safety" not in firmware_gated_reads(_LUBA_3, _FIRMWARE)


async def _push_function_codes(
    hass: HomeAssistant, coordinator: MammotionReportUpdateCoordinator
) -> None:
    device = MowingDevice()
    device.online = True
    device.device_firmwares.device_version = _FIRMWARE
    device.function_codes = _LISTED
    coordinator.manager.get_device_by_name.return_value = device
    await coordinator._on_state_changed(DeviceStateMachine("dev-1", device).current)  # noqa: SLF001
    await hass.async_block_till_done()


async def test_the_switch_is_read_once_the_function_list_arrives(
    hass: HomeAssistant,
) -> None:
    """The list is fetched after the startup reads, which therefore skipped the switch."""
    coordinator = await make_report_coordinator(hass, _LUBA_3, _FIRMWARE)
    await coordinator._async_ensure_startup_reads()  # noqa: SLF001
    coordinator.manager.read_child_safety.assert_not_awaited()

    await _push_function_codes(hass, coordinator)
    await _push_function_codes(hass, coordinator)

    coordinator.manager.read_child_safety.assert_awaited_once_with(_LUBA_3)


async def test_a_function_list_known_at_startup_is_read_with_the_rest(
    hass: HomeAssistant,
) -> None:
    """A list restored from the last session opens the gate before the first push."""
    coordinator = await make_report_coordinator(hass, _LUBA_3, _FIRMWARE)
    coordinator.data.function_codes = _LISTED

    await coordinator._async_ensure_startup_reads()  # noqa: SLF001

    coordinator.manager.read_child_safety.assert_awaited_once_with(_LUBA_3)


async def test_a_write_goes_through_the_librarys_own_flow() -> None:
    """The library picks the carrier, checks the mower's answer and re-reads."""
    coordinator = _coordinator()

    await coordinator.async_set_child_safety(True)

    coordinator.manager.set_child_safety.assert_awaited_once_with(_LUBA_3, True)


async def test_the_read_goes_through_the_librarys_own_flow() -> None:
    """The library serialises it against the other batch-config exchanges."""
    coordinator = _coordinator()

    await coordinator.async_read_child_safety()

    coordinator.manager.read_child_safety.assert_awaited_once_with(_LUBA_3)


async def test_a_write_the_mower_never_got_is_reported() -> None:
    """A switch left on after a dropped write would show a setting the mower does not have."""
    coordinator = _coordinator()
    coordinator.manager.set_child_safety.side_effect = DeviceOfflineException(
        29003, "iot-1"
    )

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_set_child_safety(True)

    assert raised.value.translation_key == "command_failed"
