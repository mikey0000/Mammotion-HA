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
    STRIPPED_NAME,
    AreaRenameRig,
    echo_of,
    make_area_rename_rig,
    set_area_name_pushes,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockEntityPlatform

from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.const import DOMAIN

_ENTITY_ID = "switch.garden_luba_vs563l6h_area_back_backyard"
_UNIQUE_ID = f"{MOWER}_{AREA_HASH}"


async def _switch_rig(hass: HomeAssistant) -> AreaRenameRig:
    """Load the switch platform for the Luba 2 whose map holds the renamed area."""
    rig = await make_area_rename_rig(hass)
    platform = MockEntityPlatform(
        hass, domain="switch", platform_name=DOMAIN, platform=switch_platform
    )
    assert await platform.async_setup_entry(rig.entry)
    await hass.async_block_till_done()
    return rig


@pytest.fixture
async def rig(hass: HomeAssistant) -> AsyncIterator[AreaRenameRig]:
    """Load the switch platform; shut the coordinator down after."""
    rig = await _switch_rig(hass)
    yield rig
    await rig.coordinator.async_shutdown()


@pytest.fixture
async def german_rig(hass: HomeAssistant) -> AsyncIterator[AreaRenameRig]:
    """Load the switch platform with Home Assistant set to German."""
    hass.config.language = "de"
    rig = await _switch_rig(hass)
    yield rig
    await rig.coordinator.async_shutdown()


async def _rename(hass: HomeAssistant, label: str | None) -> None:
    # Looked up by unique_id: the German entity_id is derived from German text.
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("switch", DOMAIN, _UNIQUE_ID)
    registry.async_update_entity(entity_id, name=label)
    await hass.async_block_till_done()


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

    await _rename(hass, ECHOED_NAME)
    await rig.receive_echo(echo_of(STRIPPED_NAME))
    await hass.async_block_till_done()

    names = {a.hash: a.name for a in rig.coordinator.data.map.area_name}
    assert names[AREA_HASH] == STRIPPED_NAME
    assert registry.async_get(_ENTITY_ID).original_name == ECHOED_NAME


@pytest.mark.regression
async def test_a_typed_area_prefix_is_not_pushed_to_the_mower(
    hass: HomeAssistant, rig: AreaRenameRig
) -> None:
    """The label "Area back backyard" was pushed whole, so the mower held "Area …".

    The area word is Home Assistant's grouping prefix; the mower's own name must
    not carry it.  The echo and the registry update after it push nothing more.
    """
    await _rename(hass, ECHOED_NAME)
    await rig.receive_echo(echo_of(STRIPPED_NAME))
    await hass.async_block_till_done()
    await _rename(hass, None)

    assert set_area_name_pushes(rig.manager) == [STRIPPED_NAME]
    assert hass.states.get(_ENTITY_ID).name == f"{MOWER} {ECHOED_NAME}"


@pytest.mark.parametrize(
    ("label", "pushed"),
    [
        pytest.param("back backyard", ["back backyard"], id="no prefix"),
        pytest.param(
            "area Back lawn",
            ["Back lawn"],
            id="lowercase prefix",
            marks=pytest.mark.regression,
        ),
        pytest.param("Areaway", ["Areaway"], id="word only begins with it"),
        pytest.param("Area", [], id="only the prefix", marks=pytest.mark.regression),
    ],
)
async def test_only_a_whole_area_word_is_stripped_before_the_push(
    hass: HomeAssistant, rig: AreaRenameRig, label: str, pushed: list[str]
) -> None:
    """The regression cases were pushed verbatim, the area word included.

    "no prefix" and "word only begins with it" are guards against stripping too
    much; a label that is only the area word renames nothing.
    """
    await _rename(hass, label)

    assert set_area_name_pushes(rig.manager) == pushed


@pytest.mark.regression
async def test_the_german_area_word_is_stripped_before_the_push(
    hass: HomeAssistant, german_rig: AreaRenameRig
) -> None:
    """In German "Bereich hinten" was pushed whole; the grouping word is "Bereich"."""
    await _rename(hass, "Bereich hinten")

    assert set_area_name_pushes(german_rig.manager) == ["hinten"]


@pytest.mark.regression
async def test_an_app_typed_area_prefix_still_renders_once(
    hass: HomeAssistant, rig: AreaRenameRig
) -> None:
    """A name typed in the app as "Area back backyard" rendered "Area Area back backyard".

    The template prefixed "Area " to a name that already had it.
    """
    await rig.receive_echo()
    await hass.async_block_till_done()

    assert er.async_get(hass).async_get(_ENTITY_ID).original_name == ECHOED_NAME
    assert hass.states.get(_ENTITY_ID).name == f"{MOWER} {ECHOED_NAME}"
    assert set_area_name_pushes(rig.manager) == []


async def test_a_stored_prefixed_label_is_not_pushed_again_after_a_restart(
    hass: HomeAssistant,
) -> None:
    """The restored label "Area back backyard" already reached the mower stripped.

    An unrelated registry update after setup must not push "back backyard" again.
    """
    rig = await make_area_rename_rig(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        _UNIQUE_ID,
        suggested_object_id=_ENTITY_ID.removeprefix("switch."),
        config_entry=rig.entry,
    )
    registry.async_update_entity(_ENTITY_ID, name=ECHOED_NAME)
    platform = MockEntityPlatform(
        hass, domain="switch", platform_name=DOMAIN, platform=switch_platform
    )
    assert await platform.async_setup_entry(rig.entry)
    await hass.async_block_till_done()

    registry.async_update_entity(_ENTITY_ID, icon="mdi:flower")
    await hass.async_block_till_done()

    assert set_area_name_pushes(rig.manager) == []
    await rig.coordinator.async_shutdown()
