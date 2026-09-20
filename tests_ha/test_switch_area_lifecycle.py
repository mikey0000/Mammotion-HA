"""Area switches: add, remove, and re-hash, against a real Home Assistant.

Scenarios kept from the stubbed suite:

* area hashes survive the JSON round trip storage does to them — they come back
  as ints even though JSON keys are strings.
* Duplicate area names (a Luba-API bug where two hashes share a name) must not
  crash during setup, when the first entity is not yet registered with HA.
* Hash-change-same-name updates the existing entity in place; no second entity,
  and the registry row is re-keyed rather than duplicated.
* Area removal takes the registry row with it and cleans the tracking dicts.
* A new area on a later call creates only that one entity.
"""

import json

from area_switch_support import (
    LUBA1,
    AreaSwitches,
    area_name,
    make_coordinator,
    set_map,
)
from homeassistant.core import HomeAssistant
from pymammotion.data.model.hash_list import HashList


class TestJsonRestoredHashes:
    """Stored maps come back through JSON, which can only key objects by string."""

    async def test_restored_area_keys_are_ints_and_create_entities(
        self, hass: HomeAssistant
    ) -> None:
        """The 19-digit hashes must survive storage and still match area_name."""
        h1, h2 = 2282538357909116923, 8744279649266345165
        coordinator = make_coordinator(
            hass,
            [h1, h2],
            [area_name("area 1", h1), area_name("area 2", h2)],
        )
        stored = json.loads(json.dumps(coordinator.data.map.to_dict()))
        coordinator.data.map = HashList.from_dict(stored)
        assert set(coordinator.data.map.area) == {h1, h2}

        switches = AreaSwitches(hass, coordinator)
        assert len(switches.sync()) == 2
        assert switches.added_areas == {h1, h2}

    async def test_restored_map_does_not_refetch_area_names(
        self, hass: HomeAssistant
    ) -> None:
        """map.area and area_name overlap exactly, so nothing is missing a name."""
        h1, h2, h3 = 2282538357909116923, 8744279649266345165, 3708098228547668094
        coordinator = make_coordinator(
            hass,
            [h1, h2, h3],
            [
                area_name("area 1", h1),
                area_name("area 3", h2),
                area_name("area 2", h3),
            ],
        )
        coordinator.data.map = HashList.from_dict(
            json.loads(json.dumps(coordinator.data.map.to_dict()))
        )

        switches = AreaSwitches(hass, coordinator)
        switches.sync()

        coordinator.async_get_area_list.assert_not_called()
        assert len(switches.by_name) == 3

    async def test_live_int_keys_behave_identically(self, hass: HomeAssistant) -> None:
        """Data straight off the device never round-trips; it must work the same."""
        h1, h2 = 111, 222
        switches = AreaSwitches(
            hass,
            make_coordinator(
                hass, [h1, h2], [area_name("zone a", h1), area_name("zone b", h2)]
            ),
        )

        assert len(switches.sync()) == 2
        assert switches.added_areas == {h1, h2}


class TestAddAreaEntities:
    """Core add/remove/update lifecycle."""

    async def test_initial_call_adds_all_areas(self, hass: HomeAssistant) -> None:
        """Every area the device reports becomes one switch."""
        h1, h2, h3 = 111, 222, 333
        switches = AreaSwitches(
            hass,
            make_coordinator(
                hass,
                [h1, h2, h3],
                [area_name("front", h1), area_name("back", h2), area_name("side", h3)],
            ),
        )

        assert len(await switches.sync_live()) == 3
        assert switches.added_areas == {h1, h2, h3}
        assert set(switches.by_name) == {"front", "back", "side"}
        assert all(switches.entity_id_for(h) for h in (h1, h2, h3))

    async def test_second_call_same_data_no_duplicates(
        self, hass: HomeAssistant
    ) -> None:
        """A repeat update with unchanged data must be a no-op."""
        h1 = 111
        switches = AreaSwitches(
            hass, make_coordinator(hass, [h1], [area_name("front", h1)])
        )

        await switches.sync_live()
        assert await switches.sync_live() == []
        assert len(switches.added) == 1

    async def test_area_removal_takes_the_registry_row_with_it(
        self, hass: HomeAssistant
    ) -> None:
        """A deleted area must leave neither tracking state nor a registry row."""
        h1, h2 = 111, 222
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("front", h1), area_name("back", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert switches.entity_id_for(h2) is not None

        set_map(coordinator, [h1], [area_name("front", h1)])
        await switches.sync_live()

        assert switches.added_areas == {h1}
        assert "back" not in switches.by_name
        assert switches.entity_id_for(h2) is None
        assert switches.entity_id_for(h1) is not None

    async def test_new_area_added_on_subsequent_call(self, hass: HomeAssistant) -> None:
        """Growing the map creates exactly the one new switch."""
        h1, h2 = 111, 222
        coordinator = make_coordinator(hass, [h1], [area_name("front", h1)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()

        set_map(coordinator, [h1, h2], [area_name("front", h1), area_name("back", h2)])
        new = await switches.sync_live()

        assert [e.area for e in new] == [h2]
        assert switches.added_areas == {h1, h2}

    async def test_none_coordinator_data_no_crash(self, hass: HomeAssistant) -> None:
        """Before the first report there is no map to read."""
        coordinator = make_coordinator(hass, [], [])
        coordinator.data = None

        assert AreaSwitches(hass, coordinator).sync() == []


class TestHashChangeSameName:
    """The device rebuilds an area: new hash, same name."""

    async def test_hash_change_updates_entity_not_duplicates(
        self, hass: HomeAssistant
    ) -> None:
        """One name is one entity, whatever hash the device gives it."""
        old_h, new_h = 111, 999
        coordinator = make_coordinator(hass, [old_h], [area_name("front yard", old_h)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        entity = switches.by_name["front yard"]

        set_map(coordinator, [new_h], [area_name("front yard", new_h)])
        assert await switches.sync_live() == []

        assert switches.by_name["front yard"] is entity
        assert entity.area == new_h
        assert switches.added_areas == {new_h}

    async def test_duplicate_name_during_setup_does_not_crash(
        self, hass: HomeAssistant
    ) -> None:
        """Two area_name entries sharing a name is a Luba-API bug.

        It takes the rename path while the first entity still has ``hass``
        unset, which used to raise "Attribute hass is None" during setup.
        """
        h1, h2, h3 = 111, 222, 333
        switches = AreaSwitches(
            hass,
            make_coordinator(
                hass,
                [h1, h2, h3],
                [
                    area_name("area 1", h1),
                    area_name("area 3", h2),
                    area_name("area 3", h3),
                ],
            ),
        )

        switches.sync()

        assert "area 1" in switches.by_name
        assert "area 3" in switches.by_name
        assert switches.by_name["area 3"].area in {h2, h3}


class TestHashOnlyChange:
    """Only the hash changed, so only the hash-derived fields may move."""

    async def _setup(
        self, hass: HomeAssistant, old_h: int, name: str = "front yard"
    ) -> tuple[object, AreaSwitches]:
        coordinator = make_coordinator(hass, [old_h], [area_name(name, old_h)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        return coordinator, switches

    async def test_area_attribute_updated_to_new_hash(
        self, hass: HomeAssistant
    ) -> None:
        """``entity.area`` is what the switch sends on, so it must move."""
        old_h, new_h = 100, 200
        coordinator, switches = await self._setup(hass, old_h)

        set_map(coordinator, [new_h], [area_name("front yard", new_h)])
        await switches.sync_live()

        assert switches.by_name["front yard"].area == new_h

    async def test_published_hash_attribute_updated(self, hass: HomeAssistant) -> None:
        """The start_mowing action resolves areas through this attribute."""
        old_h, new_h = 100, 200
        coordinator, switches = await self._setup(hass, old_h)

        set_map(coordinator, [new_h], [area_name("front yard", new_h)])
        await switches.sync_live()

        entity = switches.by_name["front yard"]
        assert entity.extra_state_attributes == {"hash": new_h}

    async def test_description_key_unchanged(self, hass: HomeAssistant) -> None:
        """The key is not the identity; ``update_area`` re-keys the unique_id."""
        old_h, new_h = 100, 200
        coordinator, switches = await self._setup(hass, old_h)
        original_key = switches.by_name["front yard"].entity_description.key

        set_map(coordinator, [new_h], [area_name("front yard", new_h)])
        await switches.sync_live()

        assert switches.by_name["front yard"].entity_description.key == original_key

    async def test_added_areas_tracks_new_hash(self, hass: HomeAssistant) -> None:
        """A lingering old hash would be reported as a removal next cycle."""
        old_h, new_h = 100, 200
        coordinator, switches = await self._setup(hass, old_h)

        set_map(coordinator, [new_h], [area_name("front yard", new_h)])
        await switches.sync_live()

        assert switches.added_areas == {new_h}

    async def test_selection_is_migrated_to_the_new_hash(
        self, hass: HomeAssistant
    ) -> None:
        """Otherwise start_mow would send a hash the device no longer knows."""
        old_h, new_h = 100, 200
        coordinator, switches = await self._setup(hass, old_h)
        coordinator.operation_settings.areas.append(old_h)

        set_map(coordinator, [new_h], [area_name("front yard", new_h)])
        await switches.sync_live()

        assert coordinator.operation_settings.areas == [new_h]

    async def test_unselected_area_is_not_added_to_the_selection(
        self, hass: HomeAssistant
    ) -> None:
        """An off switch must stay out of operation_settings.areas."""
        old_h, new_h = 100, 200
        coordinator, switches = await self._setup(hass, old_h)

        set_map(coordinator, [new_h], [area_name("front yard", new_h)])
        await switches.sync_live()

        assert coordinator.operation_settings.areas == []

    async def test_the_new_hash_reaches_the_state_machine(
        self, hass: HomeAssistant
    ) -> None:
        """``update_area`` writes state, so the dashboard sees the new hash."""
        old_h, new_h = 100, 200
        coordinator, switches = await self._setup(hass, old_h)
        entity_id = switches.by_name["front yard"].entity_id
        assert hass.states.get(entity_id).attributes["hash"] == old_h

        set_map(coordinator, [new_h], [area_name("front yard", new_h)])
        await switches.sync_live()

        assert hass.states.get(entity_id).attributes["hash"] == new_h

    async def test_no_new_entity_added_to_ha(self, hass: HomeAssistant) -> None:
        """The update is in place, so async_add_entities gets nothing."""
        old_h, new_h = 100, 200
        coordinator, switches = await self._setup(hass, old_h)

        set_map(coordinator, [new_h], [area_name("front yard", new_h)])

        assert switches.sync() == []


class TestHashUpdateCorrectness:
    """Named and unnamed areas take different routes to the same result.

    Named areas are matched by name and updated in place.  Unnamed ones have no
    name to match on, so the old hash leaves tracking and a new entity arrives.
    """

    async def test_named_area_hash_change_updates_the_entity(
        self, hass: HomeAssistant
    ) -> None:
        """``update_area`` must move both the hash and the state it publishes."""
        old_h, new_h = 1000, 9000
        coordinator = make_coordinator(hass, [old_h], [area_name("back lawn", old_h)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        entity = switches.by_name["back lawn"]
        assert entity.area == old_h
        assert hass.states.get(entity.entity_id).attributes["hash"] == old_h

        set_map(coordinator, [new_h], [area_name("back lawn", new_h)])
        await switches.sync_live()

        assert entity.area == new_h
        assert hass.states.get(entity.entity_id).attributes["hash"] == new_h

    async def test_named_area_hash_change_creates_no_second_entity(
        self, hass: HomeAssistant
    ) -> None:
        """The same object must stay in place, with nothing added beside it."""
        old_h, new_h = 1000, 9000
        coordinator = make_coordinator(hass, [old_h], [area_name("front lawn", old_h)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        entity = switches.by_name["front lawn"]

        set_map(coordinator, [new_h], [area_name("front lawn", new_h)])
        assert await switches.sync_live() == []

        assert switches.by_name["front lawn"] is entity
        assert len(switches.added) == 1

    async def test_named_area_hash_change_updates_added_areas(
        self, hass: HomeAssistant
    ) -> None:
        """added_areas swaps the hash; the old one must not linger."""
        old_h, new_h = 1000, 9000
        coordinator = make_coordinator(hass, [old_h], [area_name("side lawn", old_h)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()

        set_map(coordinator, [new_h], [area_name("side lawn", new_h)])
        await switches.sync_live()

        assert switches.added_areas == {new_h}

    async def test_named_area_hash_change_carries_selection(
        self, hass: HomeAssistant
    ) -> None:
        """A selected area stays selected under its new hash."""
        old_h, new_h = 1000, 9000
        coordinator = make_coordinator(hass, [old_h], [area_name("orchard", old_h)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        coordinator.operation_settings.areas.append(old_h)

        set_map(coordinator, [new_h], [area_name("orchard", new_h)])
        await switches.sync_live()

        assert coordinator.operation_settings.areas == [new_h]

    async def test_named_area_hash_change_only_affects_changed_area(
        self, hass: HomeAssistant
    ) -> None:
        """One area rebuilding must not disturb its neighbours."""
        h1, old_h2, new_h2, h3 = 10, 20, 200, 30
        coordinator = make_coordinator(
            hass,
            [h1, old_h2, h3],
            [area_name("alpha", h1), area_name("beta", old_h2), area_name("gamma", h3)],
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        before = {n: switches.by_name[n] for n in ("alpha", "beta", "gamma")}

        set_map(
            coordinator,
            [h1, new_h2, h3],
            [area_name("alpha", h1), area_name("beta", new_h2), area_name("gamma", h3)],
        )
        assert await switches.sync_live() == []

        assert {n: switches.by_name[n] for n in before} == before
        assert before["alpha"].area == h1
        assert before["beta"].area == new_h2
        assert before["gamma"].area == h3

    async def test_unnamed_area_hash_change_swaps_tracking(
        self, hass: HomeAssistant
    ) -> None:
        """No name to match on, so the old hash leaves and the new one arrives."""
        h1, h2, h3 = 50, 100, 999
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("", h1), area_name("", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert switches.added_areas == {h1, h2}

        set_map(coordinator, [h3, h2], [area_name("", h3), area_name("", h2)])
        await switches.sync_live()

        assert switches.added_areas == {h2, h3}

    async def test_unnamed_area_hash_change_new_entity_has_correct_hash(
        self, hass: HomeAssistant
    ) -> None:
        """The replacement entity must publish the replacement hash."""
        h1, h2, h3 = 50, 100, 999
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("", h1), area_name("", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()

        set_map(coordinator, [h3, h2], [area_name("", h3), area_name("", h2)])
        await switches.sync_live()

        new_entity = next(e for e in switches.by_name.values() if e.area == h3)
        assert hass.states.get(new_entity.entity_id).attributes["hash"] == h3

    async def test_unnamed_area_hash_change_removes_the_old_registry_row(
        self, hass: HomeAssistant
    ) -> None:
        """The stale row is what forces a "_2" suffix on the next restart."""
        h1, h2, h3 = 50, 100, 999
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("", h1), area_name("", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert switches.entity_id_for(h1) is not None

        set_map(coordinator, [h3, h2], [area_name("", h3), area_name("", h2)])
        await switches.sync_live()

        assert switches.entity_id_for(h1) is None

    async def test_unnamed_area_total_count_unchanged_after_hash_swap(
        self, hass: HomeAssistant
    ) -> None:
        """One hash in, one hash out — no net gain or loss of switches."""
        h1, h2, h3 = 50, 100, 999
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("", h1), area_name("", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert len(switches.by_name) == 2

        set_map(coordinator, [h3, h2], [area_name("", h3), area_name("", h2)])
        await switches.sync_live()

        assert len(switches.by_name) == 2
        assert len(switches.added_areas) == 2


class TestAreaListFetchTrigger:
    """``async_get_area_list`` fills in names the device has not sent yet."""

    async def test_missing_name_entry_triggers_fetch(self, hass: HomeAssistant) -> None:
        """h2 is in map.area with no area_name entry, so names are incomplete."""
        h1, h2 = 111, 222
        coordinator = make_coordinator(hass, [h1, h2], [area_name("front", h1)])

        AreaSwitches(hass, coordinator).sync()
        await hass.async_block_till_done()

        coordinator.async_get_area_list.assert_awaited_once()

    async def test_all_names_present_no_fetch(self, hass: HomeAssistant) -> None:
        """Every hash already has an entry, so there is nothing to ask for."""
        h1, h2 = 111, 222
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("front", h1), area_name("back", h2)]
        )

        AreaSwitches(hass, coordinator).sync()

        coordinator.async_get_area_list.assert_not_called()

    async def test_luba1_never_triggers_fetch(self, hass: HomeAssistant) -> None:
        """Luba 1 has no area-name command to answer the request."""
        coordinator = make_coordinator(hass, [111, 222], [], device_name=LUBA1)

        AreaSwitches(hass, coordinator).sync()

        coordinator.async_get_area_list.assert_not_called()

    async def test_empty_area_dict_no_fetch(self, hass: HomeAssistant) -> None:
        """No hashes means no names to fetch."""
        coordinator = make_coordinator(hass, [], [])

        AreaSwitches(hass, coordinator).sync()

        coordinator.async_get_area_list.assert_not_called()

    async def test_empty_names_are_not_missing_names(self, hass: HomeAssistant) -> None:
        """An entry with ``name=""`` still covers its hash; a re-fetch would loop."""
        h1, h2 = 111, 222
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("", h1), area_name("", h2)]
        )

        AreaSwitches(hass, coordinator).sync()

        coordinator.async_get_area_list.assert_not_called()


class TestEarlyReturnOnNoChange:
    """An unchanged map must cost nothing; a changed one must not be skipped."""

    async def test_same_hashes_same_names_no_entities_added(
        self, hass: HomeAssistant
    ) -> None:
        """Every report frame re-runs this, so the common case must be free."""
        h1, h2 = 111, 222
        switches = AreaSwitches(
            hass,
            make_coordinator(
                hass, [h1, h2], [area_name("Front", h1), area_name("Back", h2)]
            ),
        )
        assert len(await switches.sync_live()) == 2

        assert await switches.sync_live() == []

    async def test_same_hashes_same_names_tracking_unchanged(
        self, hass: HomeAssistant
    ) -> None:
        """The name → entity map must be identical after a no-op cycle."""
        h = 555
        switches = AreaSwitches(
            hass, make_coordinator(hass, [h], [area_name("Garden", h)])
        )
        await switches.sync_live()
        snapshot = dict(switches.by_name)

        await switches.sync_live()

        assert switches.by_name == snapshot

    async def test_name_change_is_not_skipped(self, hass: HomeAssistant) -> None:
        """A rename on the device must reach the entity."""
        h = 777
        coordinator = make_coordinator(hass, [h], [area_name("Old Name", h)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        entity = switches.by_name["Old Name"]

        set_map(coordinator, [h], [area_name("New Name", h)])
        await switches.sync_live()

        assert switches.by_name == {"New Name": entity}
        assert entity.entity_description.name == "New Name"

    async def test_new_hash_is_not_skipped(self, hass: HomeAssistant) -> None:
        """A second area appearing must not be mistaken for no change."""
        h1, h2 = 111, 222
        coordinator = make_coordinator(hass, [h1], [area_name("Front", h1)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()

        set_map(coordinator, [h1, h2], [area_name("Front", h1), area_name("Back", h2)])
        new = await switches.sync_live()

        assert [e.area for e in new] == [h2]

    async def test_removed_hash_is_not_skipped(self, hass: HomeAssistant) -> None:
        """A deletion must not be mistaken for no change either."""
        h1, h2 = 111, 222
        coordinator = make_coordinator(
            hass, [h1, h2], [area_name("Front", h1), area_name("Back", h2)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        assert switches.added_areas == {h1, h2}

        set_map(coordinator, [h1], [area_name("Front", h1)])
        await switches.sync_live()

        assert switches.added_areas == {h1}
