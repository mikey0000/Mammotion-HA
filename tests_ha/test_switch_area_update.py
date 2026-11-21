"""``MammotionConfigAreaSwitchEntity.async_update`` refreshes the switch and nothing else."""

import pytest
from area_switch_support import AreaSwitches, area_name, make_coordinator, set_map
from homeassistant.core import HomeAssistant


@pytest.mark.regression
async def test_an_update_while_the_area_is_missing_keeps_the_switch(
    hass: HomeAssistant,
) -> None:
    """The switch removed itself when its hash was missing from the map.

    Its hash stayed tracked, so it was never re-added: the switch was gone until a reload.
    Removal belongs to the map update, which also drops the tracking.
    """
    coordinator = make_coordinator(hass, [100, 200], [area_name("Front", 100)])
    switches = AreaSwitches(hass, coordinator)
    await switches.sync_live()
    entity = switches.entities[100]

    set_map(coordinator, [200], [])
    await entity.async_update()

    state = hass.states.get(entity.entity_id)
    assert state is not None, "the switch left Home Assistant"
    assert state.state == "off"


async def test_an_update_reads_the_selection(hass: HomeAssistant) -> None:
    """The selection lives on the coordinator; an update must pick up a change made there."""
    coordinator = make_coordinator(hass, [100], [area_name("Front", 100)])
    switches = AreaSwitches(hass, coordinator)
    await switches.sync_live()
    entity = switches.entities[100]

    coordinator.operation_settings.areas.append(100)
    await entity.async_update()

    assert hass.states.get(entity.entity_id).state == "on"
