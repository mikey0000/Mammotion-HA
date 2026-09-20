"""Which areas ``start_mow`` receives, and in what order.

The device mows the areas in the order the list gives them, so this is
user-visible: toggling "Back" then "Front" must not silently sort into
"Front, Back".  Both routes into ``operation_settings.areas`` are covered —
the switches themselves, and the ``areas:`` list of the start_mowing action,
which reads each entity's ``hash`` attribute off the state machine.
"""

from area_switch_support import AreaSwitches, area_name, make_coordinator
from homeassistant.core import HomeAssistant

from custom_components.mammotion.lawn_mower import get_entity_attribute


def _service_areas(hass: HomeAssistant, entity_ids: list[str]) -> list[int]:
    """Resolve an ``areas:`` list the way ``async_start_mowing`` does."""
    attributes = [
        int(entity_hash)
        for entity_id in entity_ids
        if (entity_hash := get_entity_attribute(hass, entity_id, "hash")) is not None
    ]
    return list(dict.fromkeys(attributes))


async def _three_areas(hass: HomeAssistant) -> tuple[object, AreaSwitches, list[int]]:
    hashes = [10, 20, 30]
    coordinator = make_coordinator(hass, hashes, [area_name("", h) for h in hashes])
    switches = AreaSwitches(hass, coordinator)
    await switches.sync_live()
    return coordinator, switches, hashes


class TestToggleOrder:
    """``operation_settings.areas`` follows the order the user switched areas on."""

    async def test_toggle_order_is_preserved(self, hass: HomeAssistant) -> None:
        """Switching on 3, 1, 2 must not come out sorted."""
        coordinator, switches, (h1, h2, h3) = await _three_areas(hass)

        for name in ("Area 3", "Area 1", "Area 2"):
            await switches.by_name[name].async_turn_on()

        assert coordinator.operation_settings.areas == [h3, h1, h2]

    async def test_switching_an_area_on_twice_adds_it_once(
        self, hass: HomeAssistant
    ) -> None:
        """A second turn_on (from a restored state, say) must not duplicate."""
        coordinator, switches, (h1, h2, _) = await _three_areas(hass)

        await switches.by_name["Area 1"].async_turn_on()
        await switches.by_name["Area 2"].async_turn_on()
        await switches.by_name["Area 1"].async_turn_on()

        assert coordinator.operation_settings.areas == [h1, h2]

    async def test_turning_a_middle_area_off_keeps_the_rest_in_order(
        self, hass: HomeAssistant
    ) -> None:
        """Only that hash leaves; the others must not be re-ordered."""
        coordinator, switches, (h1, _, h3) = await _three_areas(hass)

        for name in ("Area 1", "Area 2", "Area 3"):
            await switches.by_name[name].async_turn_on()
        await switches.by_name["Area 2"].async_turn_off()

        assert coordinator.operation_settings.areas == [h1, h3]

    async def test_reselecting_an_area_puts_it_last(self, hass: HomeAssistant) -> None:
        """Order is selection order, not the order the areas were created in."""
        coordinator, switches, (h1, h2, h3) = await _three_areas(hass)

        for name in ("Area 1", "Area 2", "Area 3"):
            await switches.by_name[name].async_turn_on()
        await switches.by_name["Area 1"].async_turn_off()
        await switches.by_name["Area 1"].async_turn_on()

        assert coordinator.operation_settings.areas == [h2, h3, h1]


class TestServiceAreaOrder:
    """The action's ``areas:`` list is read off the live entity states."""

    async def test_the_caller_supplied_order_wins(self, hass: HomeAssistant) -> None:
        """The entity_ids come in the order the user wrote them."""
        _, switches, (h1, h2, h3) = await _three_areas(hass)
        entity_ids = [
            switches.by_name[n].entity_id for n in ("Area 3", "Area 1", "Area 2")
        ]

        assert _service_areas(hass, entity_ids) == [h3, h1, h2]

    async def test_a_repeated_entity_keeps_its_first_position(
        self, hass: HomeAssistant
    ) -> None:
        """A duplicate entity_id must not mow the same area twice."""
        _, switches, (h1, h2, _) = await _three_areas(hass)
        area_1, area_2 = switches.by_name["Area 1"], switches.by_name["Area 2"]

        entity_ids = [area_1.entity_id, area_2.entity_id, area_1.entity_id]

        assert _service_areas(hass, entity_ids) == [h1, h2]

    async def test_a_single_area_yields_a_single_element_list(
        self, hass: HomeAssistant
    ) -> None:
        """The one-area case is the common one from the dashboard."""
        _, switches, (h1, _, _) = await _three_areas(hass)

        assert _service_areas(hass, [switches.by_name["Area 1"].entity_id]) == [h1]

    async def test_an_entity_without_a_hash_is_skipped(
        self, hass: HomeAssistant
    ) -> None:
        """A non-area entity_id in the list must not abort the whole call."""
        _, switches, (h1, _, _) = await _three_areas(hass)
        hass.states.async_set("switch.not_an_area", "off")

        entity_ids = ["switch.not_an_area", switches.by_name["Area 1"].entity_id]

        assert _service_areas(hass, entity_ids) == [h1]
