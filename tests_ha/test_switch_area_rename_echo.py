"""The mower's echo of an area rename reaches the area switch, end to end.

A real ``DeviceHandle`` decodes the captured cloud frame and runs it through the
library's reducer, state machine and map-updated bus into a report coordinator
built by its own ``__init__``; the switch platform is set up for real on a real
entity platform and registry.  Only the client is a spec'd stand-in.
"""

from collections.abc import AsyncIterator

import pytest
from area_rename_support import (
    AREA_HASH,
    ECHOED_NAME,
    MOWER,
    AreaRenameRig,
    make_area_rename_rig,
    set_area_name_pushes,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockEntityPlatform

from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.const import DOMAIN

_ENTITY_ID = "switch.garden_luba_vs563l6h_area_back_backyard"


@pytest.fixture
async def rig(hass: HomeAssistant) -> AsyncIterator[AreaRenameRig]:
    """Load the switch platform for the Luba 2 whose map holds the renamed area."""
    rig = await make_area_rename_rig(hass)
    platform = MockEntityPlatform(
        hass, domain="switch", platform_name=DOMAIN, platform=switch_platform
    )
    assert await platform.async_setup_entry(rig.entry)
    await hass.async_block_till_done()
    yield rig
    await rig.coordinator.async_shutdown()


@pytest.mark.regression
async def test_the_echoed_rename_updates_the_switch_name(
    hass: HomeAssistant, rig: AreaRenameRig
) -> None:
    """The mower's rename echo reached the map but never the entity's name.

    ``update_name`` swapped ``entity_description`` but Home Assistant caches
    ``Entity.name`` and ``translation_placeholders`` and clears them only on an
    ``_attr_`` write, so the state write kept the old name and the registry's
    ``original_name`` stayed on the device's previous area name.
    """
    registry = er.async_get(hass)
    assert registry.async_get(_ENTITY_ID).original_name == "Area Back backyard ", (
        "premise: the entity starts on the device's old name"
    )

    registry.async_update_entity(_ENTITY_ID, name=ECHOED_NAME)
    await hass.async_block_till_done()
    assert set_area_name_pushes(rig.manager) == [ECHOED_NAME]

    await rig.receive_echo()
    await hass.async_block_till_done()

    names = {a.hash: a.name for a in rig.coordinator.data.map.area_name}
    assert names[AREA_HASH] == ECHOED_NAME
    assert registry.async_get(_ENTITY_ID).original_name == ECHOED_NAME
    assert set_area_name_pushes(rig.manager) == [ECHOED_NAME], "echo re-pushed"


@pytest.mark.regression
async def test_a_typed_area_prefix_is_rendered_once_after_the_echo(
    hass: HomeAssistant, rig: AreaRenameRig
) -> None:
    """Typing "Area back backyard" came back from the mower as "Area Area back backyard".

    The template prefixed "Area " to the echoed name, which already had it; that
    is what the switch read as once the user's override was cleared.
    """
    registry = er.async_get(hass)
    registry.async_update_entity(_ENTITY_ID, name=ECHOED_NAME)
    await hass.async_block_till_done()
    await rig.receive_echo()
    await hass.async_block_till_done()

    registry.async_update_entity(_ENTITY_ID, name=None)
    await hass.async_block_till_done()

    assert hass.states.get(_ENTITY_ID).name == f"{MOWER} {ECHOED_NAME}"
    assert set_area_name_pushes(rig.manager) == [ECHOED_NAME], "a second push"
