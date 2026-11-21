"""A re-hashed area keeps its switch, matched by the stable id pymammotion reports.

Newer mowers (Luba 3 seen) give an area a new hash on a map re-save while its
``name_time.create_time`` stays put.  A named area is matched old-to-new by name;
an unnamed one is not, so its switch is deleted and a new one minted under the
next free entity_id, and the untouched unnamed neighbour is renumbered.  The dev
instance's registry shows this three times in nine days for the two unnamed
areas of a Luba-VA, while its five named areas kept their switches.
"""

from unittest.mock import MagicMock

import pytest
from area_switch_support import (
    MOWER,
    AreaSwitches,
    area_name,
    make_coordinator,
    register_area_row,
    set_map,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.model.hash_list import (
    FrameList,
    NavGetCommData,
    NavGetHashListData,
    NavNameTime,
)

from custom_components.mammotion.const import DOMAIN

NAMED, UNNAMED_A, UNNAMED_B, UNNAMED_A_REHASHED = 10, 20, 30, 21
_CREATED = {NAMED: 1_778_603_157, UNNAMED_A: 1_783_885_114, UNNAMED_B: 1_783_885_600}


def _frames(
    area_hash: int, created: int, name: str = "", *, area_id: int = 0
) -> FrameList:
    """One complete boundary frame carrying the name/creation stamp the mower sends."""
    frame = NavGetCommData(
        hash=area_hash,
        paternal_hash_b=area_id,
        total_frame=1,
        current_frame=1,
        name_time=NavNameTime(name=name, create_time=created),
    )
    return FrameList(total_frame=1, data=[frame])


def _root_list(*area_hashes: int) -> NavGetHashListData:
    """Build the device's area root hash list, which keeps a re-hashed area in place."""
    return NavGetHashListData(
        pver=1, sub_cmd=0, total_frame=1, current_frame=1, data_couple=list(area_hashes)
    )


async def _rehash_unnamed_a(hass: HomeAssistant) -> AreaSwitches:
    """Set up one named and two unnamed areas, then re-hash the first unnamed one."""
    coordinator = make_coordinator(hass, [], [area_name("Baksida", NAMED)])
    coordinator.data.map.area = {
        NAMED: _frames(NAMED, _CREATED[NAMED], "Baksida"),
        UNNAMED_A: _frames(UNNAMED_A, _CREATED[UNNAMED_A]),
        UNNAMED_B: _frames(UNNAMED_B, _CREATED[UNNAMED_B]),
    }
    coordinator.data.map.update_root_hash_list(_root_list(NAMED, UNNAMED_A, UNNAMED_B))
    switches = AreaSwitches(hass, coordinator)
    await switches.sync_live()

    # update_hash_lists rebuilds map.area by filtering, so the new hash lands last.
    coordinator.data.map.area = {
        NAMED: _frames(NAMED, _CREATED[NAMED], "Baksida"),
        UNNAMED_B: _frames(UNNAMED_B, _CREATED[UNNAMED_B]),
        UNNAMED_A_REHASHED: _frames(UNNAMED_A_REHASHED, _CREATED[UNNAMED_A]),
    }
    coordinator.data.map.update_root_hash_list(
        _root_list(NAMED, UNNAMED_A_REHASHED, UNNAMED_B)
    )
    return switches


@pytest.mark.regression
async def test_a_rehashed_unnamed_area_keeps_its_entity_id(hass: HomeAssistant) -> None:
    """The re-hashed area lost its entity_id: its row was dropped and a new one minted.

    Its only identity was the fallback "Area N" label, which ``async_add_area_entities``
    refuses to match on, so dashboards and automations lost the switch.
    """
    switches = await _rehash_unnamed_a(hass)
    before = switches.entity_id_for(UNNAMED_A)
    assert before is not None

    await switches.sync_live()

    assert switches.entity_id_for(UNNAMED_A_REHASHED) == before


@pytest.mark.regression
async def test_an_untouched_unnamed_area_keeps_its_label(hass: HomeAssistant) -> None:
    """The neighbour's fallback number was reassigned by map order ("Area 2" became "Area 1").

    The label is the only name an unnamed area has in Home Assistant, so renumbering
    it points the user's "Area 2" at a different patch of lawn.
    """
    switches = await _rehash_unnamed_a(hass)
    entity_id = switches.entity_id_for(UNNAMED_B)
    label_before = hass.states.get(entity_id).attributes["friendly_name"]

    await switches.sync_live()

    assert switches.entity_id_for(UNNAMED_B) == entity_id
    assert hass.states.get(entity_id).attributes["friendly_name"] == label_before


def _show(coordinator: MagicMock, areas: list[tuple[int, int, str]]) -> None:
    """Make the map hold *areas* as (hash, Luba 3 area id, name), listed in that order."""
    coordinator.data.map.area = {
        h: _frames(h, 1_700_000_000 + area_id, name, area_id=area_id)
        for h, area_id, name in areas
    }
    coordinator.data.map.update_root_hash_list(_root_list(*(h for h, _, _ in areas)))


async def _luba3(
    hass: HomeAssistant, areas: list[tuple[int, int, str]]
) -> AreaSwitches:
    """Set up a Luba 3 whose map holds *areas*, with their switches registered."""
    coordinator = make_coordinator(hass, [], [])
    _show(coordinator, areas)
    switches = AreaSwitches(hass, coordinator)
    await switches.sync_live()
    return switches


def _area_rows(hass: HomeAssistant) -> list[str]:
    return sorted(
        e.entity_id
        for e in er.async_get(hass).entities.values()
        if e.platform == DOMAIN
    )


async def test_a_rehashed_named_area_keeps_its_switch(hass: HomeAssistant) -> None:
    """The new hash carries the name before the name list catches up; the id decides."""
    switches = await _luba3(hass, [(100, 1, "Baksida")])
    entity_id = switches.entity_id_for(100)

    _show(switches.coordinator, [(101, 1, "Baksida")])
    assert await switches.sync_live() == []

    assert switches.entity_id_for(101) == entity_id
    assert hass.states.get(entity_id).attributes["hash"] == 101


async def test_a_whole_map_rehash_keeps_every_switch_and_label(
    hass: HomeAssistant,
) -> None:
    """The mower can re-hash every area at once; every id still finds its switch."""
    switches = await _luba3(hass, [(100, 1, "Baksida"), (200, 2, ""), (300, 3, "")])
    before = {
        area_id: (switches.entity_id_for(h), switches.entities[h].name)
        for h, area_id in ((100, 1), (200, 2), (300, 3))
    }

    _show(switches.coordinator, [(111, 1, "Baksida"), (222, 2, ""), (333, 3, "")])
    assert await switches.sync_live() == []

    after = {
        area_id: (switches.entity_id_for(h), switches.entities[h].name)
        for h, area_id in ((111, 1), (222, 2), (333, 3))
    }
    assert after == before
    assert len(_area_rows(hass)) == 3


async def test_the_mowing_order_survives_a_rehash(hass: HomeAssistant) -> None:
    """The selection held the old hash, and moving it to the end reordered the mow."""
    switches = await _luba3(hass, [(100, 1, ""), (200, 2, ""), (300, 3, "")])
    for h in (300, 100, 200):
        await switches.entities[h].async_turn_on()

    _show(switches.coordinator, [(111, 1, ""), (200, 2, ""), (300, 3, "")])
    await switches.sync_live()

    assert switches.coordinator.operation_settings.areas == [300, 111, 200]
    entity_id = switches.entity_id_for(111)
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get(entity_id).attributes["order"] == 2


async def test_a_new_hash_waits_while_its_old_hash_is_still_listed(
    hass: HomeAssistant,
) -> None:
    """Old and new hash are both held while the map catches up; one switch, which then follows."""
    switches = await _luba3(hass, [(100, 1, "")])
    entity_id = switches.entity_id_for(100)

    _show(switches.coordinator, [(100, 1, ""), (101, 1, "")])
    assert await switches.sync_live() == []
    assert switches.added_areas == {100}

    _show(switches.coordinator, [(101, 1, "")])
    await switches.sync_live()
    assert switches.entity_id_for(101) == entity_id


async def test_two_areas_with_one_name_keep_their_own_switches(
    hass: HomeAssistant,
) -> None:
    """With area ids, repeated cycles never move either switch onto the other area."""
    switches = await _luba3(hass, [(100, 1, "Front"), (200, 2, "Front")])
    rows = {h: switches.entity_id_for(h) for h in (100, 200)}

    await switches.sync_live()
    await switches.sync_live()

    assert {h: switches.entity_id_for(h) for h in (100, 200)} == rows
    assert None not in rows.values()


async def test_a_switch_from_before_the_upgrade_is_adopted(hass: HomeAssistant) -> None:
    """The unique_id is still the hash, so the old row is found as is; it then learns its id.

    That id is what lets a later reload adopt the row once the area is re-hashed.
    """
    entity_id = register_area_row(
        hass, f"{MOWER}_100", object_id="luba_area_area_2", original_name="Area area 2"
    )
    switches = await _luba3(hass, [(100, 1, "")])
    assert er.async_get(hass).async_get(entity_id).options[DOMAIN] == {"area_id": 1}

    coordinator = make_coordinator(hass, [], [])
    _show(coordinator, [(50, 9, ""), (101, 1, "")])
    AreaSwitches(hass, coordinator).sync()

    assert switches.entity_id_for(101) == entity_id


async def test_a_reload_adopts_the_row_of_an_area_rehashed_meanwhile(
    hass: HomeAssistant,
) -> None:
    """After a reload only the registry remembers the area; its saved id finds the row.

    The label cannot: the area is unnamed and its place in the map changed.
    """
    switches = await _luba3(hass, [(50, 9, ""), (100, 1, "")])
    entity_id = switches.entity_id_for(100)

    coordinator = make_coordinator(hass, [], [])
    _show(coordinator, [(101, 1, "")])
    AreaSwitches(hass, coordinator).sync()

    assert switches.entity_id_for(101) == entity_id


async def test_a_rehashed_area_on_a_mower_without_area_ids_keeps_its_switch(
    hass: HomeAssistant,
) -> None:
    """Yuka and Luba 2 leave paternal_hash_b at 0; the creation stamp is the id there."""
    coordinator = make_coordinator(hass, [], [])
    coordinator.data.map.area = {
        100: _frames(100, 1_747_177_645),
        200: _frames(200, 1_747_089_877),
    }
    switches = AreaSwitches(hass, coordinator)
    await switches.sync_live()
    entity_id = switches.entity_id_for(100)

    coordinator.data.map.area = {
        200: _frames(200, 1_747_089_877),
        101: _frames(101, 1_747_177_645),
    }
    assert await switches.sync_live() == []

    assert switches.entity_id_for(101) == entity_id


async def test_an_area_whose_name_goes_blank_keeps_its_switch(
    hass: HomeAssistant,
) -> None:
    """Seen on a Yuka Mini: an area named once now reports no name at all."""
    switches = await _luba3(hass, [(100, 1, "Clothesline")])
    entity_id = switches.entity_id_for(100)

    _show(switches.coordinator, [(100, 1, "")])
    assert await switches.sync_live() == []

    assert switches.entity_id_for(100) == entity_id
    assert switches.entities[100].area_name == "Clothesline"


async def test_two_vanished_switches_sharing_a_name_are_not_guessed_between(
    hass: HomeAssistant,
) -> None:
    """Without area ids, a new hash named like two vanished areas is a new switch."""
    coordinator = make_coordinator(
        hass, [100, 200], [area_name("Front", 100), area_name("Front", 200)]
    )
    switches = AreaSwitches(hass, coordinator)
    await switches.sync_live()

    set_map(coordinator, [300], [area_name("Front", 300)])

    assert [e.area for e in await switches.sync_live()] == [300]


async def test_a_stale_row_saved_for_another_area_is_not_claimed_by_name(
    hass: HomeAssistant,
) -> None:
    """The row's saved id says it was a different area, whatever its label."""
    stale = register_area_row(
        hass, f"{MOWER}_100", object_id="luba_area_front", original_name="Area Front"
    )
    er.async_get(hass).async_update_entity_options(stale, DOMAIN, {"area_id": 5})

    switches = await _luba3(hass, [(300, 1, "Front")])

    assert switches.entity_id_for(300) != stale
