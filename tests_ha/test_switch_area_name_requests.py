"""When the area switches ask the mower for its area names again.

Every reply to that request is a map update, and a map update is what runs this
code, so a request that cannot be satisfied must be sent once, not on every
update: over the cloud each one is a metered send.
"""

import pytest
from area_switch_support import AreaSwitches, area_name, make_coordinator
from homeassistant.core import HomeAssistant
from pymammotion.data.model.hash_list import NavGetHashListData

FRONT, BACK, FRONT_REHASHED = 10, 20, 11


def _root_list(*area_hashes: int) -> NavGetHashListData:
    return NavGetHashListData(
        pver=1, sub_cmd=0, total_frame=1, current_frame=1, data_couple=list(area_hashes)
    )


async def _updates(hass: HomeAssistant, coordinator: object, count: int) -> None:
    """Run *count* map-update cycles, letting each one's request task finish."""
    switches = AreaSwitches(hass, coordinator)
    for _ in range(count):
        await switches.sync_live()
        await hass.async_block_till_done()


async def test_an_area_the_mower_has_not_named_is_asked_about_once(
    hass: HomeAssistant,
) -> None:
    """An unnamed area never gets a name, so asking on every update never ends."""
    coordinator = make_coordinator(hass, [FRONT, BACK], [area_name("Front", FRONT)])
    coordinator.data.map.update_root_hash_list(_root_list(FRONT, BACK))

    await _updates(hass, coordinator, 3)

    assert coordinator.async_get_area_list.await_count == 1


@pytest.mark.regression
async def test_a_stale_map_does_not_keep_asking_for_names(hass: HomeAssistant) -> None:
    """The name list named the re-hashed area, the map still held the old hash, and each reply asked again."""
    coordinator = make_coordinator(
        hass,
        [FRONT, BACK],
        [area_name("Front", FRONT_REHASHED), area_name("Back", BACK)],
    )

    await _updates(hass, coordinator, 3)

    coordinator.async_get_area_list.assert_not_awaited()


async def test_a_newly_unnamed_area_is_asked_about_again(hass: HomeAssistant) -> None:
    """A different set of missing names is a new question, not a repeat."""
    coordinator = make_coordinator(hass, [FRONT, BACK], [area_name("Front", FRONT)])
    coordinator.data.map.update_root_hash_list(_root_list(FRONT, BACK))
    switches = AreaSwitches(hass, coordinator)
    await switches.sync_live()
    await hass.async_block_till_done()

    coordinator.data.map.area_name = []
    await switches.sync_live()
    await hass.async_block_till_done()

    assert coordinator.async_get_area_list.await_count == 2
