"""The turnaround select offers U-turn only to a mower whose firmware has it.

The app sends U-turn as value 2 of the same setting as the other two modes, and
shows the row only when the cloud lists function code 003.007 for the mower's
current firmware.  That list is fetched after setup, so the option has to appear
on a later coordinator update rather than only at entity creation.
"""

import json
from pathlib import Path
from typing import Any

from battery_support import make_mower, platform_entities
from homeassistant.core import HomeAssistant
from pymammotion.data.model.function_codes import FunctionCodes
from user_command_support import without_state_machine

from custom_components.mammotion import select as select_platform
from custom_components.mammotion.models import MammotionMowerData

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_LUBA_PRO = "Luba-VS123456"
_FIRMWARE = "1.15.2.2304"
_U_TURN = FunctionCodes(product_version=_FIRMWARE, codes=["003.007"])


def _mower(function_codes: FunctionCodes | None = None) -> MammotionMowerData:
    mower = make_mower(_LUBA_PRO, _FIRMWARE)
    if function_codes is not None:
        mower.reporting_coordinator.data.function_codes = function_codes
    return mower


async def _turning_mode(mower: MammotionMowerData) -> Any:
    return (await platform_entities(select_platform, mower))["turning_mode"]


async def test_a_mower_without_the_function_code_is_not_offered_u_turn() -> None:
    """The app hides the row, and older firmware does not know value 2."""
    entity = await _turning_mode(_mower())

    assert entity.options == ["zero_turn", "multipoint"]


async def test_a_mower_with_the_function_code_is_offered_u_turn() -> None:
    """U-turn is the third option, so its position is its wire value."""
    entity = await _turning_mode(_mower(_U_TURN))

    assert entity.options == ["zero_turn", "multipoint", "u_turn"]


async def test_u_turn_appears_once_the_function_list_arrives(
    hass: HomeAssistant,
) -> None:
    """The list is fetched after the entity exists, so setup alone cannot decide it."""
    mower = _mower()
    entity = without_state_machine(await _turning_mode(mower), hass)

    mower.reporting_coordinator.data.function_codes = _U_TURN
    entity._handle_coordinator_update()

    assert "u_turn" in entity.options


async def test_a_mower_reporting_u_turn_shows_it(hass: HomeAssistant) -> None:
    """A read-back of 2 is U-turn, not a fall back to the first option."""
    mower = _mower(_U_TURN)
    entity = without_state_machine(await _turning_mode(mower), hass)

    mower.reporting_coordinator.data.mower_state.turning_mode = 2
    entity._handle_coordinator_update()

    assert entity.current_option == "u_turn"


async def test_choosing_u_turn_writes_the_apps_value(hass: HomeAssistant) -> None:
    """Selecting U-turn sends 2, the app's ``TURN_MODE_U_SHAPED``."""
    mower = _mower(_U_TURN)
    entity = without_state_machine(await _turning_mode(mower), hass)

    await entity.async_select_option("u_turn")

    mower.reporting_coordinator.async_set_turning_mode.assert_awaited_once_with(2)


async def test_a_mower_already_in_u_turn_shows_it_without_the_function_list(
    hass: HomeAssistant,
) -> None:
    """A BLE-only mower never gets a function list, but its own read-back proves the mode exists."""
    mower = _mower()
    entity = without_state_machine(await _turning_mode(mower), hass)

    mower.reporting_coordinator.data.mower_state.turning_mode = 2
    await entity.async_update()

    assert entity.current_option == "u_turn"


def test_u_turn_has_a_label() -> None:
    """Locales are held to the keys of ``strings.json`` elsewhere, so the source file is the one to pin."""
    strings = json.loads((_ROOT / "strings.json").read_text())

    assert strings["entity"]["select"]["turning_mode"]["state"]["u_turn"]
