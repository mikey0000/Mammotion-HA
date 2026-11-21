"""Two areas the mower gives the same name each keep a switch.

``async_add_area_entities`` matches an untracked area to an existing switch by name,
to follow a re-hashed area onto its new hash.  A second, distinct area carrying the
same name is taken for that re-hash: the first area's switch is moved onto it and
the first area is left with no switch while the map reports itself synced.
"""

import pytest
from area_switch_support import AreaSwitches, area_name, make_coordinator
from homeassistant.core import HomeAssistant

FRONT_A, FRONT_B = 100, 200


@pytest.mark.regression
async def test_two_areas_with_one_name_each_get_a_switch(hass: HomeAssistant) -> None:
    """The second area took over the first area's switch, so only one existed."""
    switches = AreaSwitches(
        hass,
        make_coordinator(
            hass,
            [FRONT_A, FRONT_B],
            [area_name("Front", FRONT_A), area_name("Front", FRONT_B)],
        ),
    )

    await switches.sync_live()
    await switches.sync_live()

    assert switches.entity_id_for(FRONT_A) is not None, (
        "first same-named area has no switch"
    )
    assert switches.entity_id_for(FRONT_B) is not None, (
        "second same-named area has no switch"
    )
    assert sorted(e.area for e in switches.added) == [FRONT_A, FRONT_B]
