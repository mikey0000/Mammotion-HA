"""Area switches against the entity registry Home Assistant actually keeps.

The stubbed versions of these tests handed ``switch.py`` a registry double and
asserted on its call log, so they could only say that a lookup happened — not
that the right row was re-keyed or removed.  Here the rows are real, which is
the only way to see the outcome these tests exist for: after a reload the area
must still own its original ``entity_id`` instead of a ``_2``-suffixed twin.
"""

from area_switch_support import (
    MOWER,
    AreaSwitches,
    area_name,
    make_coordinator,
    set_map,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.switch import async_remove_stale_area_entities


def _register_area_row(
    hass: HomeAssistant,
    unique_id: str,
    *,
    object_id: str,
    original_name: str | None = None,
) -> str:
    """Seed the registry with an area switch from a previous session."""
    return (
        er.async_get(hass)
        .async_get_or_create(
            "switch",
            DOMAIN,
            unique_id,
            suggested_object_id=object_id,
            original_name=original_name,
            translation_key="area",
        )
        .entity_id
    )


class TestRemoveStaleAreaEntities:
    """Removal has to find the row by the unique_id the entity registered with."""

    async def test_removes_every_named_hash(self, hass: HomeAssistant) -> None:
        """Both rows must be gone, not merely looked up."""
        coordinator = make_coordinator(hass, [], [])
        for h in (111, 222):
            _register_area_row(hass, f"{MOWER}_{h}", object_id=f"area_{h}")

        async_remove_stale_area_entities(coordinator, {111, 222})

        registry = er.async_get(hass)
        assert all(
            registry.async_get_entity_id("switch", DOMAIN, f"{MOWER}_{h}") is None
            for h in (111, 222)
        )

    async def test_a_hash_with_no_row_is_not_an_error(
        self, hass: HomeAssistant
    ) -> None:
        """An area removed before its entity was ever registered still passes here."""
        coordinator = make_coordinator(hass, [], [])
        kept = _register_area_row(hass, f"{MOWER}_111", object_id="area_111")

        async_remove_stale_area_entities(coordinator, {999})

        assert er.async_get(hass).async_get(kept) is not None

    async def test_a_nineteen_digit_hash_still_matches(
        self, hass: HomeAssistant
    ) -> None:
        """Real hashes are 19 digits; the unique_id must be built from the int."""
        h = 2282538357909116923
        coordinator = make_coordinator(hass, [], [])
        _register_area_row(hass, f"{MOWER}_{h}", object_id="area_big")

        async_remove_stale_area_entities(coordinator, {h})

        assert (
            er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{MOWER}_{h}")
            is None
        )

    async def test_removal_keys_on_unique_name_not_device_name(
        self, hass: HomeAssistant
    ) -> None:
        """``MammotionBaseEntity`` registers under ``unique_name``.

        That is not always the device name.
        """
        unique_name = f"{MOWER}_someUniquePrefix"
        h = 555
        coordinator = make_coordinator(
            hass, [], [], device_name=MOWER, unique_name=unique_name
        )
        entity_id = _register_area_row(hass, f"{unique_name}_{h}", object_id="area_555")

        async_remove_stale_area_entities(coordinator, {h})

        assert er.async_get(hass).async_get(entity_id) is None

    async def test_a_device_name_keyed_lookup_would_find_nothing(
        self, hass: HomeAssistant
    ) -> None:
        """The bug this pair guards against.

        Keying the lookup on ``device_name`` misses the row, so removal
        silently does nothing and the stale switch stays.
        """
        unique_name = f"{MOWER}_someUniquePrefix"
        h = 555
        _register_area_row(hass, f"{unique_name}_{h}", object_id="area_555")

        registry = er.async_get(hass)

        assert registry.async_get_entity_id("switch", DOMAIN, f"{MOWER}_{h}") is None
        assert registry.async_get_entity_id("switch", DOMAIN, f"{unique_name}_{h}")


class TestUniqueIdRekeyOnHashChange:
    """``update_area`` must persist the new hash, or the next restart duplicates."""

    async def test_an_unregistered_entity_updates_its_own_attribute(
        self, hass: HomeAssistant
    ) -> None:
        """During setup there is no registry row yet, only the attribute."""
        coordinator = make_coordinator(hass, [111], [area_name("Front lawn", 111)])
        switches = AreaSwitches(hass, coordinator)
        (entity,) = switches.sync()

        entity.update_area(222)

        assert entity.unique_id == f"{MOWER}_222"

    async def test_a_registered_entity_rekeys_its_registry_row(
        self, hass: HomeAssistant
    ) -> None:
        """The row keeps its entity_id, so history and customisations survive."""
        coordinator = make_coordinator(hass, [111], [area_name("Front lawn", 111)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        entity = switches.by_name["Front lawn"]
        entity_id = entity.entity_id

        entity.update_area(222)

        assert switches.entity_id_for(111) is None
        assert switches.entity_id_for(222) == entity_id

    async def test_a_collision_leaves_the_registry_untouched(
        self, hass: HomeAssistant
    ) -> None:
        """Another area already holds that hash; re-keying onto it would raise."""
        coordinator = make_coordinator(
            hass, [111, 222], [area_name("Front lawn", 111), area_name("Back", 222)]
        )
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        front, back = switches.by_name["Front lawn"], switches.by_name["Back"]

        front.update_area(222)

        assert front.area == 222
        assert switches.entity_id_for(222) == back.entity_id
        assert front.unique_id == f"{MOWER}_111"


class TestReloadDoesNotDuplicateEntities:
    """A disable/re-enable cycle must reuse the previous session's rows.

    In-memory tracking starts empty after a reload, so the name-based
    reconciliation inside ``async_add_area_entities`` never sees the previous
    session's entities.  When the device hands out new hashes for the same
    named areas a fresh unique_id is minted while the old row is still there,
    and Home Assistant registers a second entity with a ``_2`` suffix
    (``switch.garden_luba_vame9r5s_area_baksida_2``).  Each further cycle bumps
    the suffix again.
    """

    @staticmethod
    def _stale_baksida(hass: HomeAssistant, old_hash: int) -> str:
        """Return the previous session's row for the area named "Baksida"."""
        return _register_area_row(
            hass,
            f"{MOWER}_{old_hash}",
            object_id="garden_luba_test_area_baksida",
            original_name="Area Baksida",
        )

    async def test_a_named_area_with_a_new_hash_rekeys_the_stale_row(
        self, hass: HomeAssistant
    ) -> None:
        """The row moves to the new hash rather than a duplicate being minted."""
        old_h, new_h = 1079026611409949868, 373475585020721672
        entity_id = self._stale_baksida(hass, old_h)
        coordinator = make_coordinator(hass, [new_h], [area_name("Baksida", new_h)])
        switches = AreaSwitches(hass, coordinator)

        switches.sync()

        assert switches.entity_id_for(new_h) == entity_id

    async def test_the_previous_unique_id_does_not_survive(
        self, hass: HomeAssistant
    ) -> None:
        """A leftover row under the old hash is what forces the ``_2`` suffix."""
        old_h, new_h = 1079026611409949868, 373475585020721672
        self._stale_baksida(hass, old_h)
        coordinator = make_coordinator(hass, [new_h], [area_name("Baksida", new_h)])
        switches = AreaSwitches(hass, coordinator)

        switches.sync()

        assert switches.entity_id_for(old_h) is None

    async def test_an_auto_named_area_rekeys_the_same_way(
        self, hass: HomeAssistant
    ) -> None:
        """``switch.garden_luba_vame9r5s_area_area_1`` became ``..._area_1_2``."""
        old_h, new_h = 6630234128293022052, 865714081496397399
        entity_id = _register_area_row(
            hass,
            f"{MOWER}_{old_h}",
            object_id="garden_luba_test_area_area_1",
            original_name="Area Area 1",
        )
        # The device sends no name, so computed_areas auto-names it "Area 1".
        coordinator = make_coordinator(hass, [new_h], [area_name("", new_h)])
        switches = AreaSwitches(hass, coordinator)

        switches.sync()

        assert switches.entity_id_for(new_h) == entity_id

    async def test_repeated_reload_cycles_do_not_accumulate_rows(
        self, hass: HomeAssistant
    ) -> None:
        """Two cycles with a new hash each time, as in the field report."""
        h1, h2, h3 = 111, 222, 333
        entity_id = self._stale_baksida(hass, h1)

        for new_hash in (h2, h3):
            coordinator = make_coordinator(
                hass, [new_hash], [area_name("Baksida", new_hash)]
            )
            AreaSwitches(hass, coordinator).sync()

        registry = er.async_get(hass)
        rows = [e for e in registry.entities.values() if e.platform == DOMAIN]
        assert [(e.entity_id, e.unique_id) for e in rows] == [
            (entity_id, f"{MOWER}_{h3}")
        ]

    async def test_a_removed_area_is_not_rekeyed_onto_a_surviving_one(
        self, hass: HomeAssistant
    ) -> None:
        """A stale row is claimed by the area whose name it carries.

        Not by whichever area happens to be missing a row.
        """
        old_h, kept_h, new_h = 111, 222, 333
        baksida = self._stale_baksida(hass, old_h)
        coordinator = make_coordinator(hass, [kept_h], [area_name("Framsida", kept_h)])
        switches = AreaSwitches(hass, coordinator)
        await switches.sync_live()
        framsida = switches.by_name["Framsida"].entity_id
        assert framsida != baksida

        set_map(coordinator, [new_h], [area_name("Baksida", new_h)])
        await switches.sync_live()

        assert switches.entity_id_for(kept_h) is None
        assert switches.entity_id_for(new_h) == baksida
