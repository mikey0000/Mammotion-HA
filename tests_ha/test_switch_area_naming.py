"""Area switch naming: the "Area N" fallback, renames, and the duplicates they caused.

Every case here came from a user seeing a second switch appear for an area they
already had.  The stubbed suite re-implemented pymammotion's numbering to check
them; these run the real ``HashList.computed_areas`` and a real entity registry,
so a numbering change in the library shows up here rather than in the field.
"""

from area_switch_support import AreaSwitches, area_name, make_coordinator, set_map
from homeassistant.core import HomeAssistant


class TestEmptyAreaNameFallback:
    """Areas the device never named are labelled "Area N", never "Zone N".

    The prefix was briefly "Zone" during a refactor of
    ``GeojsonGenerator._NAME_TEMPLATES``; it has to match what pymammotion's
    ``computed_areas`` generates or the two disagree about the same area.
    """

    async def test_empty_names_produce_area_n_entities(
        self, hass: HomeAssistant
    ) -> None:
        """Two unnamed areas are "Area 1" and "Area 2"."""
        h1, h2 = 100, 200
        switches = AreaSwitches(
            hass,
            make_coordinator(hass, [h1, h2], [area_name("", h1), area_name("", h2)]),
        )

        assert len(await switches.sync_live()) == 2
        assert set(switches.by_name) == {"Area 1", "Area 2"}

    async def test_empty_names_never_produce_zone_n(self, hass: HomeAssistant) -> None:
        """No fallback name may begin with "Zone"."""
        hashes = [10, 20, 30]
        switches = AreaSwitches(
            hass, make_coordinator(hass, hashes, [area_name("", h) for h in hashes])
        )

        await switches.sync_live()

        assert not any(n.lower().startswith("zone") for n in switches.by_name)

    async def test_only_the_unnamed_area_gets_a_fallback(
        self, hass: HomeAssistant
    ) -> None:
        """A device-assigned name must survive alongside an unnamed sibling."""
        h_named, h_unnamed = 100, 200
        switches = AreaSwitches(
            hass,
            make_coordinator(
                hass,
                [h_named, h_unnamed],
                [area_name("Front Lawn", h_named), area_name("", h_unnamed)],
            ),
        )

        assert len(await switches.sync_live()) == 2
        assert switches.by_name["Front Lawn"].area == h_named
        assert switches.by_name["Area 1"].area == h_unnamed


class TestUnnamedAreaDeduplication:
    """Unnamed areas must not multiply across map refreshes.

    ``area_name`` can be wiped by an incoming MQTT message.  When that made
    ``all_current_areas`` collapse to ``{}``, every tracked area looked removed,
    its registry row was dropped and the next update re-created it — leaving
    the user a second set of switches to delete by hand.
    """

    async def test_repeated_call_with_the_same_unnamed_areas_adds_nothing(
        self, hass: HomeAssistant
    ) -> None:
        """The second cycle must recognise "Area 1"/"Area 2" as already present."""
        h1, h2 = 100, 200
        switches = AreaSwitches(
            hass,
            make_coordinator(hass, [h1, h2], [area_name("", h1), area_name("", h2)]),
        )

        assert len(await switches.sync_live()) == 2
        assert await switches.sync_live() == []

    async def test_a_wiped_area_name_list_removes_nothing(
        self, hass: HomeAssistant
    ) -> None:
        """map.area is the source of truth for presence, not area_name."""
        h1, h2 = 100, 200
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("", h1), area_name("", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        rows = {h: switches.entity_id_for(h) for h in (h1, h2)}

        coordinator.data.map.area_name = []
        assert await switches.sync_live() == []

        assert switches.added_areas == {h1, h2}
        assert len(switches.by_name) == 2
        assert {h: switches.entity_id_for(h) for h in (h1, h2)} == rows

        coordinator.data.map.area_name = [area_name("", h1), area_name("", h2)]
        assert await switches.sync_live() == []
        assert {h: switches.entity_id_for(h) for h in (h1, h2)} == rows

    async def test_the_fallback_numbers_survive_a_wipe_and_restore(
        self, hass: HomeAssistant
    ) -> None:
        """The same hashes must come back as the same "Area N", not 4, 5 and 6."""
        hashes = [50, 100, 200]
        coordinator = make_coordinator(hass, hashes, [area_name("", h) for h in hashes])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert set(switches.by_name) == {"Area 1", "Area 2", "Area 3"}

        coordinator.data.map.area_name = []
        await switches.sync_live()
        coordinator.data.map.area_name = [area_name("", h) for h in hashes]
        await switches.sync_live()

        assert set(switches.by_name) == {"Area 1", "Area 2", "Area 3"}
        assert switches.added_areas == set(hashes)

    async def test_an_area_deleted_on_the_device_loses_its_switch(
        self, hass: HomeAssistant
    ) -> None:
        """Deleting the middle area must remove that row and no other."""
        h1, h2, h3 = 10, 20, 30
        coordinator = make_coordinator(
            hass, [h1, h2, h3], [area_name("", h) for h in (h1, h2, h3)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert switches.added_areas == {h1, h2, h3}

        set_map(coordinator, [h1, h3], [area_name("", h1), area_name("", h3)])
        await switches.sync_live()

        assert h2 not in switches.added_areas
        assert h2 not in {e.area for e in switches.by_name.values()}
        assert switches.entity_id_for(h2) is None
        assert switches.entity_id_for(h1) is not None
        assert switches.entity_id_for(h3) is not None


class TestAreaNameUpdate:
    """A real name arriving for a tracked hash renames the switch in place."""

    async def test_unnamed_area_gets_a_name_on_a_later_update(
        self, hass: HomeAssistant
    ) -> None:
        """Naming h1 frees its number, so h2 renumbers from "Area 2" to "Area 1"."""
        h1, h2 = 10, 20
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("", h1), area_name("", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert set(switches.by_name) == {"Area 1", "Area 2"}

        coordinator.data.map.area_name = [area_name("My Garden", h1), area_name("", h2)]
        assert await switches.sync_live() == []

        assert set(switches.by_name) == {"My Garden", "Area 1"}
        assert switches.by_name["My Garden"].area == h1
        assert switches.by_name["Area 1"].area == h2
        assert len(switches.added) == 2
        entity = switches.by_name["My Garden"]
        assert entity.entity_description.name == "My Garden"
        assert entity.entity_description.translation_placeholders == {
            "name": "My Garden"
        }

    async def test_several_areas_are_named_in_one_cycle(
        self, hass: HomeAssistant
    ) -> None:
        """Both renames must land without either being read as a new area."""
        h1, h2 = 10, 20
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("", h1), area_name("", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()

        coordinator.data.map.area_name = [
            area_name("Front Lawn", h1),
            area_name("Back Garden", h2),
        ]
        assert await switches.sync_live() == []

        assert set(switches.by_name) == {"Front Lawn", "Back Garden"}
        assert switches.by_name["Front Lawn"].area == h1
        assert switches.by_name["Back Garden"].area == h2

    async def test_a_rename_leaves_the_tracked_hashes_alone(
        self, hass: HomeAssistant
    ) -> None:
        """Touching added_areas on a rename would look like a remove and re-add."""
        h1 = 10
        coordinator = make_coordinator(hass, [h1], [area_name("", h1)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert switches.added_areas == {h1}

        coordinator.data.map.area_name = [area_name("Orchard", h1)]
        await switches.sync_live()

        assert switches.added_areas == {h1}

    async def test_a_rename_keeps_the_original_registry_row(
        self, hass: HomeAssistant
    ) -> None:
        """The hash did not change, so the entity_id and its history must not."""
        h1 = 10
        coordinator = make_coordinator(hass, [h1], [area_name("", h1)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        entity_id = switches.by_name["Area 1"].entity_id

        coordinator.data.map.area_name = [area_name("Orchard", h1)]
        await switches.sync_live()

        assert switches.by_name["Orchard"].entity_id == entity_id
        assert switches.entity_id_for(h1) == entity_id


class TestAreaCounterAfterDeletion:
    """A new unnamed area must not reuse a number a live switch already holds."""

    async def test_deleting_one_area_renumbers_the_survivors(
        self, hass: HomeAssistant
    ) -> None:
        """Deleting "Area 2" frees the slot, so "Area 3" slides down into it."""
        h1, h2, h3, h4 = 10, 20, 30, 40
        coordinator = make_coordinator(
            hass, [h1, h2, h3], [area_name("", h) for h in (h1, h2, h3)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert set(switches.by_name) == {"Area 1", "Area 2", "Area 3"}

        set_map(coordinator, [h1, h3, h4], [area_name("", h) for h in (h1, h3, h4)])
        await switches.sync_live()

        assert switches.by_name["Area 1"].area == h1
        assert switches.by_name["Area 2"].area == h3
        assert switches.by_name["Area 3"].area == h4
        assert h4 in switches.added_areas
        assert h2 not in switches.added_areas

    async def test_two_deletions_renumber_the_same_way(
        self, hass: HomeAssistant
    ) -> None:
        """Gaps are filled from the lowest free number, not left open."""
        h1, h2, h3, h4, h5 = 10, 20, 30, 40, 50
        coordinator = make_coordinator(
            hass, [h1, h2, h3, h4], [area_name("", h) for h in (h1, h2, h3, h4)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert len(switches.by_name) == 4

        set_map(coordinator, [h1, h4, h5], [area_name("", h) for h in (h1, h4, h5)])
        await switches.sync_live()

        assert switches.by_name["Area 1"].area == h1
        assert switches.by_name["Area 2"].area == h4
        assert switches.by_name["Area 3"].area == h5


class TestDuplicateRegressions:
    """The two root causes behind duplicated area switches."""

    async def test_a_transiently_empty_map_keeps_every_switch(
        self, hass: HomeAssistant
    ) -> None:
        """A map refresh empties map.area for a moment.

        Dropping the registry rows there is what made the areas come back as
        brand-new entities.
        """
        h1, h2 = 100, 200
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("", h1), area_name("", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        rows = {h: switches.entity_id_for(h) for h in (h1, h2)}

        set_map(coordinator, [], [])
        assert await switches.sync_live() == []

        assert switches.added_areas == {h1, h2}
        assert {h: switches.entity_id_for(h) for h in (h1, h2)} == rows

    async def test_lowercase_pymammotion_names_do_not_collide(
        self, hass: HomeAssistant
    ) -> None:
        """Pymammotion's own fallbacks are lowercase ("area 1").

        A counter that only recognised the capitalised form restarted at 1 and
        minted an "Area 1" switch beside the existing "area 1" one.
        """
        h1, h2, h3 = 10, 20, 30
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("area 1", h1), area_name("area 2", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()

        set_map(
            coordinator,
            [h1, h2, h3],
            [area_name("area 1", h1), area_name("area 2", h2)],
        )
        new = await switches.sync_live()

        assert [e.area for e in new] == [h3]
        assert switches.added_areas == {h1, h2, h3}
        h3_name = next(n for n, e in switches.by_name.items() if e.area == h3)
        assert h3_name.lower() not in {"area 1", "area 2"}


class TestPoolAreaNoDuplication:
    """One area name is one switch, however often the device re-hashes it.

    A user's "Pool" area gained a second "Area Pool" switch after the device
    assigned it a new hash: the new hash reached ``new_areas`` without being
    matched back to the entity already serving that name.
    """

    async def _pool(self, hass: HomeAssistant, h_initial: int):
        coordinator = make_coordinator(
            hass, [h_initial], [area_name("Pool", h_initial)]
        )
        switches = AreaSwitches(hass, coordinator)
        assert len(await switches.sync_live()) == 1
        return coordinator, switches

    async def test_a_new_hash_does_not_add_a_second_switch(
        self, hass: HomeAssistant
    ) -> None:
        """Exactly one entity, one registry row and one state."""
        h1, h2 = 111_000, 222_000
        coordinator, switches = await self._pool(hass, h1)

        set_map(coordinator, [h2], [area_name("Pool", h2)])
        assert await switches.sync_live() == []

        assert len(switches.added) == 1
        assert list(switches.by_name) == ["Pool"]

    async def test_the_entity_is_updated_in_place(self, hass: HomeAssistant) -> None:
        """Same Python object, so its state history and customisations survive."""
        h1, h2 = 111_000, 222_000
        coordinator, switches = await self._pool(hass, h1)
        entity = switches.by_name["Pool"]

        set_map(coordinator, [h2], [area_name("Pool", h2)])
        await switches.sync_live()

        assert switches.by_name["Pool"] is entity

    async def test_the_registry_row_is_rekeyed_not_replaced(
        self, hass: HomeAssistant
    ) -> None:
        """The old unique_id must not survive to collide on the next restart."""
        h1, h2 = 111_000, 222_000
        coordinator, switches = await self._pool(hass, h1)
        entity_id = switches.by_name["Pool"].entity_id

        set_map(coordinator, [h2], [area_name("Pool", h2)])
        await switches.sync_live()

        assert switches.entity_id_for(h1) is None
        assert switches.entity_id_for(h2) == entity_id

    async def test_the_entity_reports_the_new_hash(self, hass: HomeAssistant) -> None:
        """start_mow reads the hash off the state, so it has to be current."""
        h1, h2 = 111_000, 222_000
        coordinator, switches = await self._pool(hass, h1)
        entity = switches.by_name["Pool"]

        set_map(coordinator, [h2], [area_name("Pool", h2)])
        await switches.sync_live()

        assert entity.area == h2
        assert hass.states.get(entity.entity_id).attributes["hash"] == h2

    async def test_the_old_hash_leaves_the_tracked_set(
        self, hass: HomeAssistant
    ) -> None:
        """A lingering old hash is read as a removal on the next cycle."""
        h1, h2 = 111_000, 222_000
        coordinator, switches = await self._pool(hass, h1)

        set_map(coordinator, [h2], [area_name("Pool", h2)])
        await switches.sync_live()

        assert switches.added_areas == {h2}

    async def test_sequential_hash_changes_do_not_accumulate(
        self, hass: HomeAssistant
    ) -> None:
        """Three rebuilds in a row still leave one switch."""
        h1, h2, h3 = 111_000, 222_000, 333_000
        coordinator, switches = await self._pool(hass, h1)

        for new_h in (h2, h3):
            set_map(coordinator, [new_h], [area_name("Pool", new_h)])
            await switches.sync_live()

        assert len(switches.added) == 1
        assert list(switches.by_name) == ["Pool"]
        assert switches.by_name["Pool"].area == h3

    async def test_the_full_lifecycle_unnamed_then_named_then_rehashed(
        self, hass: HomeAssistant
    ) -> None:
        """The common user path, which must leave one switch throughout.

        An area arrives unnamed, the device names it "Pool", then rebuilds it
        with a new hash.
        """
        h1, h2 = 111_000, 222_000
        coordinator = make_coordinator(hass, [h1], [area_name("", h1)])
        switches = AreaSwitches(hass, coordinator)
        assert len(await switches.sync_live()) == 1
        assert "Area 1" in switches.by_name

        coordinator.data.map.area_name = [area_name("Pool", h1)]
        await switches.sync_live()
        assert list(switches.by_name) == ["Pool"]

        set_map(coordinator, [h2], [area_name("Pool", h2)])
        await switches.sync_live()

        assert len(switches.added) == 1
        assert list(switches.by_name) == ["Pool"]
        assert switches.by_name["Pool"].area == h2
        assert switches.added_areas == {h2}

    async def test_a_repeated_cycle_with_the_same_data_is_idempotent(
        self, hass: HomeAssistant
    ) -> None:
        """Every report frame runs this; it must not drift."""
        h1 = 111_000
        _, switches = await self._pool(hass, h1)

        assert await switches.sync_live() == []
        assert len(switches.added) == 1
        assert len(switches.by_name) == 1
