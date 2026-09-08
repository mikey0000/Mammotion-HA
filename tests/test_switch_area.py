"""Unit tests for async_add_area_entities and async_remove_stale_area_entities.

Key scenarios validated
-----------------------
* area.keys() are **strings** in stored JSON; area_name.hash values are **ints**.
  All comparisons must still work correctly (int/str mismatch).
* Duplicate area names (Luba-API bug where two hashes share a name) must not
  crash during setup even though the second entity hasn't been registered with
  HA yet (hass is None).
* Hash-change-same-name → existing entity is updated in place, no new entity.
* Area removal → entity removed from registry, tracking dicts cleaned up.
* New area added on a subsequent call → only the new entity is created.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal stubs — imported BEFORE any custom_components import so that the
# heavy HA / pymammotion dependency chain is never executed.
# ---------------------------------------------------------------------------

import sys
import types


def _stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return sys.modules[name]


# Only add stubs that are not already present (conftest may have set some up).
_stub("aiousbwatcher", AIOUSBWatcher=MagicMock(), InotifyNotAvailableError=Exception)
_stub("bleak", BleakClient=MagicMock())
_stub("bleak.backends.device", BLEDevice=MagicMock())
_stub("bleak.exc", BleakError=Exception)
_stub("bleak_retry_connector", BleakNotFoundError=Exception)
_stub("aiohttp", ClientConnectorError=Exception)
_stub("aiohttp.web_exceptions", HTTPException=Exception)
_stub(
    "homeassistant.core",
    HomeAssistant=object,
    callback=lambda f: f,
    ServiceCall=object,
    ServiceResponse=object,
    SupportsResponse=MagicMock(),
)
_stub(
    "homeassistant.config_entries",
    ConfigEntry=object,
    ConfigFlow=object,
    ConfigFlowResult=object,
    OptionsFlow=object,
)
_stub("homeassistant.const", CONF_ADDRESS=str, CONF_PASSWORD=str, STATE_ON="on")
_stub(
    "homeassistant.components.switch",
    DOMAIN="switch",
    SwitchEntity=object,
    SwitchEntityDescription=object,
)
_stub(
    "homeassistant.components.bluetooth",
    async_ble_device_from_address=MagicMock(),
    BluetoothServiceInfo=object,
    async_discovered_service_info=MagicMock(),
)
_stub(
    "homeassistant.components.camera",
    Camera=object,
    CameraEntityDescription=object,
    WebRTCAnswer=object,
    WebRTCError=object,
    WebRTCSendMessage=object,
    CameraEntityFeature=MagicMock(),
)
_stub("homeassistant.components.web_rtc", async_register_ice_servers=MagicMock())
_stub("homeassistant.helpers.entity_registry", async_get=MagicMock())
_stub("homeassistant.helpers.entity", EntityCategory=MagicMock())
_stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_stub("homeassistant.helpers.restore_state", RestoreEntity=object)
_stub(
    "homeassistant.helpers.update_coordinator",
    CoordinatorEntity=object,
    DataUpdateCoordinator=object,
)
_stub(
    "homeassistant.helpers.device_registry",
    CONNECTION_BLUETOOTH="bluetooth",
    CONNECTION_NETWORK_MAC="mac",
    DeviceInfo=MagicMock(),
    format_mac=lambda m: m,
)
_stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=MagicMock())
_stub("webrtc_models", RTCIceCandidateInit=object, RTCIceServer=object)
_stub("pymammotion")
_stub("pymammotion.data")
_stub("pymammotion.data.model")


class _AreaHashNameList:
    def __init__(self, name: str, hash: int) -> None:  # noqa: A002
        self.name = name
        self.hash = hash


def _compute_areas_for_test(area_dict: dict, area_name_list: list) -> list:
    """Replicate HashList.computed_areas for test mocks.

    Area dict values are MagicMocks, so FrameList.name is always treated as
    empty — only area_name_list entries and auto-generation matter here.
    """
    result = [_AreaHashNameList(name=a.name, hash=a.hash) for a in area_name_list]
    area_name_hashes = {a.hash for a in result}

    def _next_n() -> int:
        used = {
            int(a.name.split()[-1])
            for a in result
            if a.name
            and a.name.lower().startswith("area ")
            and a.name.split()[-1].isdigit()
        }
        n = 1
        while n in used:
            n += 1
        return n

    for entry in result:
        if not entry.name:
            entry.name = f"Area {_next_n()}"

    for k in area_dict:
        s = str(k)
        if not s.lstrip("-").isdigit():
            continue
        hash_id = int(s)
        if hash_id in area_name_hashes:
            continue
        area_name_hashes.add(hash_id)
        result.append(_AreaHashNameList(name=f"Area {_next_n()}", hash=hash_id))

    return result


class _MapMock:
    """Minimal map mock whose computed_areas recomputes from current area/area_name."""

    def __init__(self, area_dict: dict, area_name_list: list) -> None:
        self.area = area_dict
        self.area_name = area_name_list

    @property
    def computed_areas(self) -> list:
        return _compute_areas_for_test(self.area, self.area_name)


_stub("pymammotion.data.model.hash_list", AreaHashNameList=_AreaHashNameList)
_stub("pymammotion.data.model.device", MowingDevice=MagicMock(), RTKDevice=MagicMock())
_stub(
    "pymammotion.utility",
)
_stub("pymammotion.utility.device_type", DeviceType=MagicMock())
_stub("pymammotion.utility.constant", WorkMode=MagicMock())
_stub(
    "pymammotion.aliyun.cloud_gateway",
    CheckSessionException=Exception,
    SetupException=Exception,
)
_stub("pymammotion.http.model.http", UnauthorizedException=Exception)
_stub("pymammotion.http.model.camera_stream", StreamSubscriptionResponse=MagicMock())
_stub(
    "pymammotion.mammotion.devices.mammotion_bluetooth",
    CharacteristicMissingError=Exception,
)
_stub("pymammotion.transport.base", NoTransportAvailableError=Exception)
_stub("pymammotion.client", MammotionClient=MagicMock())
_stub("custom_components")
_stub("custom_components.mammotion", MammotionConfigEntry=object, __path__=[])
_stub(
    "custom_components.mammotion.const",
    DOMAIN="mammotion",
    CONF_RETRY_COUNT="retry_count",
    DEFAULT_RETRY_COUNT=3,
    CONF_STAY_CONNECTED_BLUETOOTH="stay_connected_bluetooth",
    CONF_MOVEMENT_USE_WIFI="movement_use_wifi",
    CONF_ACCOUNTNAME="account_name",
    CONF_ACCOUNT_ID="mammotion_account_id",
    CONF_USE_WIFI="use_wifi",
    CONF_DEVICE_NAME="device_name",
    CONF_BLE_DEVICES="ble_devices",
    COMMAND_EXCEPTIONS=(Exception,),
    EXPIRED_CREDENTIAL_EXCEPTIONS=(Exception,),
    NO_REQUEST_MODES=(),
    LOGGER=MagicMock(),
)
_stub(
    "custom_components.mammotion.coordinator",
    MammotionBaseUpdateCoordinator=object,
    MammotionReportUpdateCoordinator=object,
    MammotionRTKCoordinator=object,
)
class _StubBaseEntity:
    """Carries HA Entity's class-level defaults the production base provides."""

    hass = None
    registry_entry = None


_stub(
    "custom_components.mammotion.entity",
    MammotionBaseEntity=_StubBaseEntity,
    MammotionBaseRTKEntity=object,
    MammotionCameraBaseEntity=object,
)

import importlib.util
from pathlib import Path

_switch_path = (
    Path(__file__).parent.parent / "custom_components" / "mammotion" / "switch.py"
)
_spec = importlib.util.spec_from_file_location(
    "custom_components.mammotion.switch", _switch_path
)
_switch_mod = importlib.util.module_from_spec(_spec)
sys.modules["custom_components.mammotion.switch"] = _switch_mod
_spec.loader.exec_module(_switch_mod)

async_add_area_entities = _switch_mod.async_add_area_entities
async_remove_stale_area_entities = _switch_mod.async_remove_stale_area_entities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _area_name(name: str, hash_val: int) -> _AreaHashNameList:
    return _AreaHashNameList(name=name, hash=hash_val)


def _make_coordinator(
    area_dict: dict,
    area_name_list: list,
    device_name: str = "Luba-TEST",
    is_luba1: bool = False,
) -> MagicMock:
    """Build a minimal coordinator mock.

    ``area_dict`` keys may be **int or str** to simulate both live and
    JSON-restored data.  ``coord.data.map`` is a :class:`_MapMock` so that
    ``computed_areas`` recomputes dynamically whenever ``area`` or ``area_name``
    are reassigned between calls.
    """
    coord = MagicMock()
    coord.unique_name = device_name
    coord.device_name = device_name
    coord.operation_settings.areas = []
    coord.data.map = _MapMock(area_dict, area_name_list)
    coord.hass = MagicMock()

    # Patch DeviceType.is_luba1 to return the supplied value
    _switch_mod.DeviceType.is_luba1 = MagicMock(return_value=is_luba1)
    return coord


# ---------------------------------------------------------------------------
# Tests: int/str key handling
# ---------------------------------------------------------------------------


class TestIntStrKeyHandling:
    """area.keys() are strings when data is restored from JSON storage."""

    def test_string_area_keys_creates_entities(self) -> None:
        """Entities are created even when area.keys() are strings."""
        h1, h2 = 2282538357909116923, 8744279649266345165
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("area 1", h1), _area_name("area 2", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 2
        assert added_areas == {h1, h2}

    def test_string_keys_vs_int_name_hashes_no_duplicate_fetch(self) -> None:
        """map_area_hashes and area_name_hashes overlap exactly — no spurious
        async_get_area_list() call when keys are strings but hashes are ints."""
        h1, h2, h3 = 2282538357909116923, 8744279649266345165, 3708098228547668094
        coord = _make_coordinator(
            area_dict={
                str(h1): MagicMock(),
                str(h2): MagicMock(),
                str(h3): MagicMock(),
            },
            area_name_list=[
                _area_name("area 1", h1),
                _area_name("area 3", h2),
                _area_name("area 2", h3),
            ],
        )
        added_areas: set[int] = set()
        by_name: dict = {}

        async_add_area_entities(coord, added_areas, by_name, [].extend)

        # No unknown hashes → async_get_area_list must NOT be called
        coord.hass.async_create_task.assert_not_called()
        assert len(by_name) == 3

    def test_int_area_keys_also_work(self) -> None:
        """Live data (int keys) must work identically."""
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={h1: MagicMock(), h2: MagicMock()},
            area_name_list=[_area_name("zone a", h1), _area_name("zone b", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 2
        assert added_areas == {h1, h2}


# ---------------------------------------------------------------------------
# Tests: normal lifecycle
# ---------------------------------------------------------------------------


class TestAddAreaEntities:
    """Core add/remove/update lifecycle."""

    def test_initial_call_adds_all_areas(self) -> None:
        h1, h2, h3 = 111, 222, 333
        coord = _make_coordinator(
            area_dict={
                str(h1): MagicMock(),
                str(h2): MagicMock(),
                str(h3): MagicMock(),
            },
            area_name_list=[
                _area_name("front", h1),
                _area_name("back", h2),
                _area_name("side", h3),
            ],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 3
        assert added_areas == {h1, h2, h3}
        assert set(by_name.keys()) == {"front", "back", "side"}

    def test_second_call_same_data_no_duplicates(self) -> None:
        h1 = 111
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock()},
            area_name_list=[_area_name("front", h1)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        async_add_area_entities(coord, added_areas, by_name, added.extend)
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 1  # second call must not add anything

    def test_area_removal_cleans_tracking(self) -> None:
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("front", h1), _area_name("back", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)
        assert added_areas == {h1, h2}

        # Remove h2 from device
        coord.data.map.area = {str(h1): MagicMock()}
        coord.data.map.area_name = [_area_name("front", h1)]

        with patch.object(
            _switch_mod, "async_remove_stale_area_entities"
        ) as mock_remove:
            async_add_area_entities(coord, added_areas, by_name, [].extend)
            mock_remove.assert_called_once_with(coord, {h2})

        assert added_areas == {h1}
        assert "back" not in by_name

    def test_new_area_added_on_subsequent_call(self) -> None:
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock()},
            area_name_list=[_area_name("front", h1)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        # Device grows a new area
        coord.data.map.area = {str(h1): MagicMock(), str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("front", h1), _area_name("back", h2)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 2
        assert added_areas == {h1, h2}

    def test_none_coordinator_data_no_crash(self) -> None:
        coord = MagicMock()
        coord.data = None
        async_add_area_entities(coord, set(), {}, [].extend)  # must not raise


# ---------------------------------------------------------------------------
# Tests: hash-change / same-name update
# ---------------------------------------------------------------------------


class TestHashChangeSameName:
    """When device rebuilds an area (new hash, same name)."""

    def test_hash_change_updates_entity_not_duplicates(self) -> None:
        old_h, new_h = 111, 999
        coord = _make_coordinator(
            area_dict={str(old_h): MagicMock()},
            area_name_list=[_area_name("front yard", old_h)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert len(added) == 1
        assert old_h in added_areas

        # Simulate entity registered with HA so hass is not None
        by_name["front yard"].hass = MagicMock()

        # Hash changes, same name
        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front yard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        # No new entity created — entity was updated in place
        assert len(added) == 1
        assert new_h in added_areas
        assert old_h not in added_areas
        assert by_name["front yard"].area == new_h

    def test_duplicate_name_from_luba_api_bug_does_not_crash(self) -> None:
        """Two area_name entries with the same name (Luba-API bug) must not
        crash even though the first entity has hass=None during setup."""
        h1, h2, h3 = 111, 222, 333
        coord = _make_coordinator(
            area_dict={
                str(h1): MagicMock(),
                str(h2): MagicMock(),
                str(h3): MagicMock(),
            },
            area_name_list=[
                _area_name("area 1", h1),
                _area_name("area 3", h2),  # duplicate name "area 3"
                _area_name("area 3", h3),  # same name, different hash
            ],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        # Must not raise RuntimeError: "Attribute hass is None"
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        # One entity per unique name — one "area 3" entity, one "area 1" entity
        assert "area 1" in by_name
        assert "area 3" in by_name
        # The "area 3" entity should hold one of the two hashes
        assert by_name["area 3"].area in {h2, h3}


# ---------------------------------------------------------------------------
# Tests: hash-only change updates entity fields
# ---------------------------------------------------------------------------


class TestHashOnlyChange:
    """When only the hash changes (same name, same area), every field on the
    entity must reflect the new hash — nothing else should change."""

    def _setup(self, old_h: int, new_h: int, name: str = "front yard"):
        """Return (coord, added_areas, by_name, entity) after initial setup."""
        coord = _make_coordinator(
            area_dict={str(old_h): MagicMock()},
            area_name_list=[_area_name(name, old_h)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)
        entity = by_name[name]
        entity.hass = MagicMock()
        return coord, added_areas, by_name, entity

    def test_area_attribute_updated_to_new_hash(self) -> None:
        """entity.area must equal the new hash after the update."""
        old_h, new_h = 100, 200
        coord, added_areas, by_name, _ = self._setup(old_h, new_h)

        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front yard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        assert by_name["front yard"].area == new_h

    def test_extra_state_attributes_hash_updated(self) -> None:
        """entity._attr_extra_state_attributes must expose the new hash value."""
        old_h, new_h = 100, 200
        coord, added_areas, by_name, _ = self._setup(old_h, new_h)

        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front yard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        assert by_name["front yard"]._attr_extra_state_attributes == {"hash": new_h}

    def test_unique_id_key_unchanged(self) -> None:
        """entity_description.key stays put on a hash update.

        The unique_id is re-keyed separately by ``_async_rekey_unique_id`` —
        see :class:`TestUniqueIdRekeyOnHashChange`.
        """
        old_h, new_h = 100, 200
        coord, added_areas, by_name, entity = self._setup(old_h, new_h)
        original_key = entity.entity_description.key

        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front yard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        assert by_name["front yard"].entity_description.key == original_key

    def test_added_areas_tracks_new_hash(self) -> None:
        """added_areas must contain new_h and not old_h after the update."""
        old_h, new_h = 100, 200
        coord, added_areas, by_name, _ = self._setup(old_h, new_h)

        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front yard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        assert new_h in added_areas
        assert old_h not in added_areas

    def test_operation_settings_areas_migrated_when_selected(self) -> None:
        """If the area was selected (old_h in operation_settings.areas), the
        new hash must replace it so start_mow sends the correct hash."""
        old_h, new_h = 100, 200
        coord, added_areas, by_name, entity = self._setup(old_h, new_h)
        coord.operation_settings.areas.append(old_h)

        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front yard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        assert new_h in coord.operation_settings.areas
        assert old_h not in coord.operation_settings.areas

    def test_operation_settings_areas_untouched_when_not_selected(self) -> None:
        """If the area was not selected, operation_settings.areas must stay empty."""
        old_h, new_h = 100, 200
        coord, added_areas, by_name, _ = self._setup(old_h, new_h)
        # old_h not added to areas — entity is off

        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front yard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        assert coord.operation_settings.areas == []

    def test_async_write_ha_state_called(self) -> None:
        """async_write_ha_state must be called on the entity when hass is set."""
        old_h, new_h = 100, 200
        coord, added_areas, by_name, entity = self._setup(old_h, new_h)
        entity.async_write_ha_state = MagicMock()

        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front yard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        entity.async_write_ha_state.assert_called()

    def test_no_new_entity_added_to_ha(self) -> None:
        """async_add_entities must not receive a new entity — update is in place."""
        old_h, new_h = 100, 200
        coord, added_areas, by_name, _ = self._setup(old_h, new_h)
        newly_added: list = []

        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front yard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, newly_added.extend)

        assert newly_added == [], "No new entity must be passed to async_add_entities"


# ---------------------------------------------------------------------------
# Tests: async_remove_stale_area_entities
# ---------------------------------------------------------------------------


class TestAsyncRemoveEntities:
    def _make_registry(self, unique_name: str, hashes: list[int]) -> MagicMock:
        registry = MagicMock()

        def _get(domain, integration, unique_id):
            for h in hashes:
                if unique_id == f"{unique_name}_{h}":
                    return f"switch.{unique_name}_{h}"
            return None

        registry.async_get_entity_id.side_effect = _get
        return registry

    def test_removes_by_hash(self) -> None:
        coord = MagicMock()
        coord.unique_name = "Luba-TEST"
        coord.device_name = "Luba-TEST"
        coord.hass = MagicMock()
        registry = self._make_registry("Luba-TEST", [111, 222])

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            async_remove_stale_area_entities(coord, {111, 222})

        assert registry.async_remove.call_count == 2

    def test_missing_entity_no_crash(self) -> None:
        coord = MagicMock()
        coord.unique_name = "Luba-TEST"
        coord.device_name = "Luba-TEST"
        coord.hass = MagicMock()
        registry = MagicMock()
        registry.async_get_entity_id.return_value = None

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            async_remove_stale_area_entities(coord, {999})

        registry.async_remove.assert_not_called()

    def test_string_hash_keys_in_removal(self) -> None:
        """Removal uses the int hash — must still find entities registered
        with int-based unique_ids even if original area keys were strings."""
        coord = MagicMock()
        coord.unique_name = "Luba-TEST"
        coord.device_name = "Luba-TEST"
        coord.hass = MagicMock()
        h = 2282538357909116923
        registry = self._make_registry("Luba-TEST", [h])

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            async_remove_stale_area_entities(coord, {h})

        registry.async_remove.assert_called_once_with(f"switch.Luba-TEST_{h}")


# ---------------------------------------------------------------------------
# Tests: unnamed-area deduplication & removal using correct unique_name
# ---------------------------------------------------------------------------


class TestUnnamedAreaDeduplication:
    """Unnamed areas (auto-named 'Area 1', 'Area 2', ...) must not duplicate.

    Root causes being exercised:
    * async_remove_stale_area_entities looks up entities by coordinator.device_name, but
      MammotionBaseEntity registers them with coordinator.unique_name.  When
      unique_name != device_name the registry lookup returns None and removal
      silently does nothing, leaving stale entities in HA.
    * area_name_list can be wiped by incoming MQTT messages; when it becomes
      empty all_current_areas collapses to {}, every tracked area is treated
      as 'old', removal is attempted (and fails per the above), tracking is
      cleared, and the next update re-creates the same entities — producing
      duplicates the user has to remove manually.
    """

    def _make_coordinator_unique_differs(
        self,
        area_dict: dict,
        area_name_list: list,
        device_name: str = "Luba-ABC123",
        unique_name: str = "Luba-ABC123_unique",
        is_luba1: bool = False,
    ) -> MagicMock:
        """Coordinator where unique_name != device_name (the realistic case)."""
        coord = MagicMock()
        coord.device_name = device_name
        coord.unique_name = unique_name
        coord.operation_settings.areas = []
        coord.data.map = _MapMock(area_dict, area_name_list)
        coord.hass = MagicMock()
        _switch_mod.DeviceType.is_luba1 = MagicMock(return_value=is_luba1)
        return coord

    # ------------------------------------------------------------------
    # unnamed areas: no duplication on repeated calls
    # ------------------------------------------------------------------

    def test_unnamed_areas_no_duplicate_on_repeated_call(self) -> None:
        """Calling async_add_area_entities twice with the same unnamed areas
        must not create any new entities on the second call."""
        h1, h2 = 100, 200
        # area_name entries with empty names → auto-assigned "Area 1", "Area 2"
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[
                _area_name("", h1),
                _area_name("", h2),
            ],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        async_add_area_entities(coord, added_areas, by_name, added.extend)
        first_count = len(added)
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert first_count == 2
        assert len(added) == 2, (
            "Second call with identical data must not create additional entities"
        )

    def test_unnamed_areas_no_duplicate_when_area_name_list_wiped(self) -> None:
        """If area_name_list is transiently wiped (empty) between updates, areas
        must not be removed or duplicated — map.area is the source of truth for
        presence, so entities stay until the hash disappears from map.area."""
        h1, h2 = 100, 200
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        # Call 1: areas arrive with (unnamed) entries in area_name_list
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert len(added) == 2

        # Simulate area_name_list being transiently wiped by an incoming MQTT message.
        # map.area still has both hashes, so all_current_areas is unchanged.
        coord.data.map.area_name = []

        with patch.object(
            _switch_mod, "async_remove_stale_area_entities"
        ) as mock_remove:
            # Call 2: area_name_list empty but map.area intact → no change
            async_add_area_entities(coord, added_areas, by_name, added.extend)
            mock_remove.assert_not_called()

        # Entities and tracking must be unchanged
        assert added_areas == {h1, h2}
        assert len(by_name) == 2
        assert len(added) == 2  # no new entities created

        # Restore area_name_list (device re-sends it)
        coord.data.map.area_name = [_area_name("", h1), _area_name("", h2)]

        # Call 3: data restored — still no new entities, no removals
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert len(added) == 2, (
            "Restoring area_name_list must not create duplicate entities"
        )
        assert added_areas == {h1, h2}
        assert len(by_name) == 2

    # ------------------------------------------------------------------
    # removal uses unique_name, not device_name
    # ------------------------------------------------------------------

    def test_removal_uses_unique_name_not_device_name(self) -> None:
        """async_remove_stale_area_entities must build the registry lookup key from
        coordinator.unique_name, which is what MammotionBaseEntity uses for
        _attr_unique_id.  Using device_name instead silently misses entities."""
        device_name = "Luba-ABC123"
        unique_name = "Luba-ABC123_someUniquePrefix"
        h = 555

        coord = MagicMock()
        coord.device_name = device_name
        coord.unique_name = unique_name
        coord.hass = MagicMock()

        registry = MagicMock()

        # Simulate entity registered under unique_name, NOT device_name
        def _get(domain, integration, uid):
            if uid == f"{unique_name}_{h}":
                return f"switch.entity_{h}"
            return None  # device_name-based lookup returns nothing

        registry.async_get_entity_id.side_effect = _get

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            async_remove_stale_area_entities(coord, {h})

        # With the current bug (device_name), async_remove is never called.
        # This assertion documents the correct expectation and will fail until fixed.
        registry.async_remove.assert_called_once_with(f"switch.entity_{h}")

    def test_removal_with_device_name_misses_entity(self) -> None:
        """Demonstrates the current bug: when unique_name != device_name the
        entity is NOT removed because the registry lookup uses device_name."""
        device_name = "Luba-ABC123"
        unique_name = "Luba-ABC123_someUniquePrefix"
        h = 555

        coord = MagicMock()
        coord.device_name = device_name
        coord.unique_name = unique_name
        coord.hass = MagicMock()

        registry = MagicMock()
        # Only recognise the unique_name-based ID
        registry.async_get_entity_id.return_value = None  # device_name lookup fails

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            async_remove_stale_area_entities(coord, {h})

        # Because the lookup fails, nothing is removed — silent data loss
        registry.async_remove.assert_not_called()

    # ------------------------------------------------------------------
    # unnamed area counter stability across wipe/restore
    # ------------------------------------------------------------------

    def test_unnamed_area_counter_stable_after_wipe_restore(self) -> None:
        """After area_name_list is wiped and restored, auto-generated area names
        must be consistent (same hashes → same 'Area N' labels, same count)."""
        h1, h2, h3 = 50, 100, 200  # sorted order determines counter
        coord = _make_coordinator(
            area_dict={
                str(h1): MagicMock(),
                str(h2): MagicMock(),
                str(h3): MagicMock(),
            },
            area_name_list=[_area_name("", h1), _area_name("", h2), _area_name("", h3)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        async_add_area_entities(coord, added_areas, by_name, added.extend)
        # Sorted order: h1=50 → Area 1, h2=100 → Area 2, h3=200 → Area 3
        assert set(by_name.keys()) == {"Area 1", "Area 2", "Area 3"}

        # Wipe and clear tracking (simulating what call-2 does)
        coord.data.map.area_name = []
        with patch.object(_switch_mod, "async_remove_stale_area_entities"):
            async_add_area_entities(coord, added_areas, by_name, added.extend)

        # Restore
        coord.data.map.area_name = [
            _area_name("", h1),
            _area_name("", h2),
            _area_name("", h3),
        ]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        # Must produce the same three names, not Area 4/5/6
        assert set(by_name.keys()) == {"Area 1", "Area 2", "Area 3"}
        assert len(added_areas) == 3

    def test_area_removed_from_device_triggers_removal(self) -> None:
        """When an area disappears from the device (no longer in area_name_list
        or map.area), async_remove_stale_area_entities must be called with that hash."""
        h1, h2, h3 = 10, 20, 30
        coord = _make_coordinator(
            area_dict={
                str(h1): MagicMock(),
                str(h2): MagicMock(),
                str(h3): MagicMock(),
            },
            area_name_list=[_area_name("", h1), _area_name("", h2), _area_name("", h3)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)
        assert added_areas == {h1, h2, h3}

        # h2 is deleted from the device
        coord.data.map.area = {str(h1): MagicMock(), str(h3): MagicMock()}
        coord.data.map.area_name = [_area_name("", h1), _area_name("", h3)]

        with patch.object(
            _switch_mod, "async_remove_stale_area_entities"
        ) as mock_remove:
            async_add_area_entities(coord, added_areas, by_name, [].extend)
            mock_remove.assert_called_once_with(coord, {h2})

        assert h2 not in added_areas
        assert h2 not in {e.area for e in by_name.values()}


# ---------------------------------------------------------------------------
# Tests: hash-change correctness for named and unnamed areas
# ---------------------------------------------------------------------------


class TestHashUpdateCorrectness:
    """Validate that both named and unnamed areas carry the correct hash after
    the device rebuilds an area (same logical area, new hash value).

    Named areas  — matched by name → entity updated in-place via update_area().
    Unnamed areas — no name to match on → treated as area removal + new addition;
                    old hash must leave tracking, new hash must enter it, and the
                    new entity must expose the correct hash.
    """

    # ------------------------------------------------------------------
    # Named areas
    # ------------------------------------------------------------------

    def test_named_area_hash_change_updates_area_attribute(self) -> None:
        """Entity.area must reflect the new hash after update_area() is called."""
        old_h, new_h = 1000, 9000
        coord = _make_coordinator(
            area_dict={str(old_h): MagicMock()},
            area_name_list=[_area_name("back lawn", old_h)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        entity = by_name["back lawn"]
        assert entity.area == old_h
        assert entity._attr_extra_state_attributes == {"hash": old_h}

        # Simulate hass assignment so async_write_ha_state doesn't raise
        entity.hass = MagicMock()

        # Device rebuilds the area — same name, new hash
        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("back lawn", new_h)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert entity.area == new_h
        assert entity._attr_extra_state_attributes == {"hash": new_h}

    def test_named_area_hash_change_no_new_entity(self) -> None:
        """Hash change on a named area must not produce a new entity object."""
        old_h, new_h = 1000, 9000
        coord = _make_coordinator(
            area_dict={str(old_h): MagicMock()},
            area_name_list=[_area_name("front lawn", old_h)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        entity_before = by_name["front lawn"]

        entity_before.hass = MagicMock()
        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("front lawn", new_h)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 1, "No new entity should be added on hash change"
        assert by_name["front lawn"] is entity_before, "Must be the same entity object"

    def test_named_area_hash_change_updates_added_areas(self) -> None:
        """added_areas must swap old hash for new hash; old must not linger."""
        old_h, new_h = 1000, 9000
        coord = _make_coordinator(
            area_dict={str(old_h): MagicMock()},
            area_name_list=[_area_name("side lawn", old_h)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)
        assert old_h in added_areas

        by_name["side lawn"].hass = MagicMock()
        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("side lawn", new_h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        assert new_h in added_areas
        assert old_h not in added_areas

    def test_named_area_hash_change_carries_selection(self) -> None:
        """If the area was selected (in operation_settings.areas) before the
        hash change, the new hash should replace the old one in that set."""
        old_h, new_h = 1000, 9000
        coord = _make_coordinator(
            area_dict={str(old_h): MagicMock()},
            area_name_list=[_area_name("orchard", old_h)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        # Mark the area as selected
        coord.operation_settings.areas.append(old_h)

        by_name["orchard"].hass = MagicMock()
        coord.data.map.area = {str(new_h): MagicMock()}
        coord.data.map.area_name = [_area_name("orchard", new_h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        assert new_h in coord.operation_settings.areas
        assert old_h not in coord.operation_settings.areas

    def test_named_area_hash_change_only_affects_changed_area(self) -> None:
        """When one of several named areas changes hash, the others are untouched."""
        h1, old_h2, new_h2, h3 = 10, 20, 200, 30
        coord = _make_coordinator(
            area_dict={
                str(h1): MagicMock(),
                str(old_h2): MagicMock(),
                str(h3): MagicMock(),
            },
            area_name_list=[
                _area_name("alpha", h1),
                _area_name("beta", old_h2),
                _area_name("gamma", h3),
            ],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        for name in ("alpha", "beta", "gamma"):
            by_name[name].hass = MagicMock()

        entity_alpha = by_name["alpha"]
        entity_beta = by_name["beta"]
        entity_gamma = by_name["gamma"]

        # Only "beta" changes hash
        coord.data.map.area = {
            str(h1): MagicMock(),
            str(new_h2): MagicMock(),
            str(h3): MagicMock(),
        }
        coord.data.map.area_name = [
            _area_name("alpha", h1),
            _area_name("beta", new_h2),
            _area_name("gamma", h3),
        ]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 3, "No new entities should be created"
        assert by_name["alpha"] is entity_alpha
        assert by_name["beta"] is entity_beta
        assert by_name["gamma"] is entity_gamma
        assert entity_alpha.area == h1
        assert entity_beta.area == new_h2
        assert entity_gamma.area == h3

    # ------------------------------------------------------------------
    # Unnamed areas
    # ------------------------------------------------------------------

    def test_unnamed_area_hash_change_new_hash_in_tracking(self) -> None:
        """After an unnamed area's hash changes, the new hash must be present
        in added_areas and the old hash must be gone."""
        h1, h2, h3 = 50, 100, 999  # h1 is replaced by h3; h2 stays
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert added_areas == {h1, h2}

        # h1 replaced by h3 on the device
        coord.data.map.area = {str(h3): MagicMock(), str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("", h3), _area_name("", h2)]

        with patch.object(_switch_mod, "async_remove_stale_area_entities"):
            async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert h3 in added_areas, "New hash must be tracked"
        assert h1 not in added_areas, "Old hash must be removed from tracking"
        assert h2 in added_areas, "Unchanged hash must remain"

    def test_unnamed_area_hash_change_new_entity_has_correct_hash(self) -> None:
        """The entity created for a replacement unnamed area must store the new hash."""
        h1, h2, h3 = 50, 100, 999
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        coord.data.map.area = {str(h3): MagicMock(), str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("", h3), _area_name("", h2)]

        with patch.object(_switch_mod, "async_remove_stale_area_entities"):
            async_add_area_entities(coord, added_areas, by_name, added.extend)

        # Find the entity serving h3
        new_entity = next((e for e in by_name.values() if e.area == h3), None)
        assert new_entity is not None, "An entity for the new hash must exist"
        assert new_entity._attr_extra_state_attributes == {"hash": h3}

    def test_unnamed_area_hash_change_triggers_removal_of_old(self) -> None:
        """When an unnamed area's hash changes, async_remove_stale_area_entities must be
        called with the old hash so the stale entity is cleaned from HA."""
        h1, h2, h3 = 50, 100, 999
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        coord.data.map.area = {str(h3): MagicMock(), str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("", h3), _area_name("", h2)]

        with patch.object(
            _switch_mod, "async_remove_stale_area_entities"
        ) as mock_remove:
            async_add_area_entities(coord, added_areas, by_name, [].extend)
            mock_remove.assert_called_once_with(coord, {h1})

    def test_unnamed_area_total_count_unchanged_after_hash_swap(self) -> None:
        """Replacing one unnamed area hash with another must keep the total
        entity count the same — no net gain or loss."""
        h1, h2, h3 = 50, 100, 999  # h1 → h3
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert len(by_name) == 2

        coord.data.map.area = {str(h3): MagicMock(), str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("", h3), _area_name("", h2)]

        with patch.object(_switch_mod, "async_remove_stale_area_entities"):
            async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(by_name) == 2, "Total entity count must remain 2 after one hash swap"
        assert len(added_areas) == 2


# ---------------------------------------------------------------------------
# Tests: name update for already-tracked hash
# ---------------------------------------------------------------------------


class TestAreaNameUpdate:
    """When area_name_list gains a real name for an already-tracked hash, the
    existing switch entity must be renamed in place — no duplicate created."""

    def test_unnamed_area_gets_name_on_subsequent_update(self) -> None:
        """Area starts unnamed ('Area 1'), then area_name_list provides 'My Garden'.
        The existing entity must be renamed; no new entity should be created.

        When h1 gets the real device name 'My Garden', computed_areas reassigns
        h2 from 'Area 2' to 'Area 1' (gap-fill: it is now the first unnamed area).
        """
        h1, h2 = 10, 20
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert set(by_name.keys()) == {"Area 1", "Area 2"}
        assert len(added) == 2

        # Device sends a real name for h1
        coord.data.map.area_name = [_area_name("My Garden", h1), _area_name("", h2)]

        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert "My Garden" in by_name, "Entity must be re-keyed to the new name"
        assert by_name["My Garden"].area == h1, "Renamed entity must still track h1"
        # h2 is renumbered to 'Area 1' (it is now the only unnamed area).
        assert "Area 1" in by_name, "h2 must be renumbered to 'Area 1'"
        assert by_name["Area 1"].area == h2
        assert "Area 2" not in by_name, "Old auto-name for h2 must be gone"
        assert len(by_name) == 2, "No duplicate entity must be created"
        assert len(added) == 2, "No new entity must be added to HA"
        assert by_name["My Garden"].entity_description.name == "My Garden"
        assert by_name["My Garden"].entity_description.translation_placeholders == {
            "name": "My Garden"
        }

    def test_multiple_areas_get_names_simultaneously(self) -> None:
        """Both unnamed areas receive real names in the same update cycle."""
        h1, h2 = 10, 20
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        coord.data.map.area_name = [
            _area_name("Front Lawn", h1),
            _area_name("Back Garden", h2),
        ]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert set(by_name.keys()) == {"Front Lawn", "Back Garden"}
        assert by_name["Front Lawn"].area == h1
        assert by_name["Back Garden"].area == h2
        assert len(added) == 2, "No new entities created when only names change"

    def test_name_update_does_not_affect_added_areas_set(self) -> None:
        """Renaming must not alter added_areas — the hash set must be unchanged."""
        h1 = 10
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock()},
            area_name_list=[_area_name("", h1)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)
        assert added_areas == {h1}

        coord.data.map.area_name = [_area_name("Orchard", h1)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        assert added_areas == {h1}, "added_areas must be unchanged after a rename"


# ---------------------------------------------------------------------------
# Tests: area counter after deletion
# ---------------------------------------------------------------------------


class TestAreaCounterAfterDeletion:
    """New unnamed areas must not reuse numbers already assigned to existing
    switches, even after a deletion shrinks len(added_areas)."""

    def test_new_area_gets_next_number_after_deletion(self) -> None:
        """After deleting 'Area 2', computed_areas renumbers surviving areas
        to fill the gap: 'Area 3' becomes 'Area 2', and the new area gets 'Area 3'.
        """
        h1, h2, h3, h4 = 10, 20, 30, 40

        # Initial state: three unnamed areas — will be named Area 1, Area 2, Area 3
        coord = _make_coordinator(
            area_dict={
                str(h1): MagicMock(),
                str(h2): MagicMock(),
                str(h3): MagicMock(),
            },
            area_name_list=[_area_name("", h1), _area_name("", h2), _area_name("", h3)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert set(by_name.keys()) == {"Area 1", "Area 2", "Area 3"}
        assert len(added_areas) == 3

        # Delete h2 ("Area 2"), then add h4 (new unnamed area)
        coord.data.map.area = {
            str(h1): MagicMock(),
            str(h3): MagicMock(),
            str(h4): MagicMock(),
        }
        coord.data.map.area_name = [
            _area_name("", h1),
            _area_name("", h3),
            _area_name("", h4),
        ]

        with patch.object(_switch_mod, "async_remove_stale_area_entities"):
            async_add_area_entities(coord, added_areas, by_name, added.extend)

        # computed_areas gap-fills: h3 (was "Area 3") → "Area 2", h4 (new) → "Area 3"
        assert "Area 3" in by_name, (
            "New area gets 'Area 3' (renumbered into freed slot)"
        )
        assert by_name["Area 3"].area == h4
        assert h4 in added_areas
        assert "Area 2" in by_name
        assert by_name["Area 2"].area == h3, "h3 renumbered from 'Area 3' to 'Area 2'"
        assert h2 not in added_areas

    def test_new_area_counter_skips_gaps_from_multiple_deletions(self) -> None:
        """After deleting areas 2 and 3, computed_areas renumbers: h4 → 'Area 2',
        new h5 → 'Area 3'."""
        h1, h2, h3, h4, h5 = 10, 20, 30, 40, 50

        # Setup: 4 unnamed areas → Area 1 .. Area 4
        coord = _make_coordinator(
            area_dict={str(h): MagicMock() for h in (h1, h2, h3, h4)},
            area_name_list=[_area_name("", h) for h in (h1, h2, h3, h4)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert len(by_name) == 4

        # Delete h2 and h3, add h5
        coord.data.map.area = {str(h): MagicMock() for h in (h1, h4, h5)}
        coord.data.map.area_name = [_area_name("", h) for h in (h1, h4, h5)]

        with patch.object(_switch_mod, "async_remove_stale_area_entities"):
            async_add_area_entities(coord, added_areas, by_name, added.extend)

        # h4 renumbered from "Area 4" to "Area 2"; h5 takes "Area 3"
        assert "Area 2" in by_name
        assert by_name["Area 2"].area == h4
        assert "Area 3" in by_name
        assert by_name["Area 3"].area == h5
        assert "Area 1" in by_name
        assert by_name["Area 1"].area == h1


# ---------------------------------------------------------------------------
# Tests: area selection order is preserved for start_mow
# ---------------------------------------------------------------------------


def _toggle(entity, on: bool) -> None:
    """Call an area entity's set_fn directly, simulating a switch toggle."""
    entity.entity_description.set_fn(entity.coordinator, on, entity.area)


class TestAreaSelectionOrder:
    """Area hashes sent to the device must match the order in which the user
    selected (toggled on) the switches, and duplicates must be silently dropped."""

    # ------------------------------------------------------------------
    # Path 1: operational_settings populated via switch toggles
    # ------------------------------------------------------------------

    def test_toggle_order_preserved_in_operation_settings(self) -> None:
        """Toggling areas on in order h3→h1→h2 must produce areas=[h3, h1, h2]."""
        h1, h2, h3 = 10, 20, 30
        coord = _make_coordinator(
            area_dict={str(h): MagicMock() for h in (h1, h2, h3)},
            area_name_list=[_area_name("", h) for h in (h1, h2, h3)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        # Toggle on in a deliberate non-sorted order
        _toggle(by_name["Area 3"], on=True)
        _toggle(by_name["Area 1"], on=True)
        _toggle(by_name["Area 2"], on=True)

        assert coord.operation_settings.areas == [h3, h1, h2], (
            "Areas must be in toggle-on order, not sorted"
        )

    def test_toggle_dedup_no_duplicate_hash(self) -> None:
        """Toggling the same switch on twice must not add a duplicate hash."""
        h1, h2 = 10, 20
        coord = _make_coordinator(
            area_dict={str(h): MagicMock() for h in (h1, h2)},
            area_name_list=[_area_name("", h) for h in (h1, h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        _toggle(by_name["Area 1"], on=True)
        _toggle(by_name["Area 2"], on=True)
        _toggle(by_name["Area 1"], on=True)  # duplicate — must be ignored

        assert coord.operation_settings.areas == [h1, h2], (
            "Duplicate toggle must not append a second copy of the hash"
        )

    def test_toggle_off_removes_from_correct_position(self) -> None:
        """Turning off a middle area removes only that hash; order of remainder unchanged."""
        h1, h2, h3 = 10, 20, 30
        coord = _make_coordinator(
            area_dict={str(h): MagicMock() for h in (h1, h2, h3)},
            area_name_list=[_area_name("", h) for h in (h1, h2, h3)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        _toggle(by_name["Area 1"], on=True)
        _toggle(by_name["Area 2"], on=True)
        _toggle(by_name["Area 3"], on=True)
        _toggle(by_name["Area 2"], on=False)

        assert coord.operation_settings.areas == [h1, h3], (
            "Removing middle area must preserve relative order of remaining areas"
        )

    def test_readd_after_removal_appends_to_end(self) -> None:
        """Re-enabling a previously deselected area appends it at the end."""
        h1, h2, h3 = 10, 20, 30
        coord = _make_coordinator(
            area_dict={str(h): MagicMock() for h in (h1, h2, h3)},
            area_name_list=[_area_name("", h) for h in (h1, h2, h3)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        _toggle(by_name["Area 1"], on=True)
        _toggle(by_name["Area 2"], on=True)
        _toggle(by_name["Area 3"], on=True)
        _toggle(by_name["Area 1"], on=False)
        _toggle(by_name["Area 1"], on=True)  # re-add at end

        assert coord.operation_settings.areas == [h2, h3, h1], (
            "Re-added area must appear at end, not its original position"
        )

    # ------------------------------------------------------------------
    # Path 2: areas passed via the start_mow service (kwargs)
    # ------------------------------------------------------------------

    def test_service_areas_order_preserved(self) -> None:
        """Areas provided to start_mow via kwargs preserve the caller-supplied order."""
        h1, h2, h3 = 10, 20, 30
        # Simulate the transformation in async_start_mowing:
        #   attributes = [int(hash) for entity_id in entity_ids if hash is not None]
        #   operational_settings.areas = list(dict.fromkeys(attributes))
        entity_ids_in_order = ["entity.area_3", "entity.area_1", "entity.area_2"]
        hash_by_entity = {
            "entity.area_1": h1,
            "entity.area_2": h2,
            "entity.area_3": h3,
        }

        attributes = [hash_by_entity[eid] for eid in entity_ids_in_order]
        areas = list(dict.fromkeys(attributes))

        assert areas == [h3, h1, h2], (
            "Service call area order must match entity_ids order, not be sorted"
        )

    def test_service_areas_dedup_preserves_first_occurrence(self) -> None:
        """Duplicate entity IDs in the service call drop later occurrences."""
        h1, h2 = 10, 20
        entity_ids = ["entity.area_1", "entity.area_2", "entity.area_1"]
        hash_by_entity = {"entity.area_1": h1, "entity.area_2": h2}

        attributes = [hash_by_entity[eid] for eid in entity_ids]
        areas = list(dict.fromkeys(attributes))

        assert areas == [h1, h2], (
            "Duplicate entity in service call must not produce duplicate hash"
        )

    def test_service_areas_single_item_list(self) -> None:
        """A single-area service call produces a single-element list."""
        h1 = 10
        attributes = [h1]
        areas = list(dict.fromkeys(attributes))
        assert areas == [h1]


# ---------------------------------------------------------------------------
# Tests: duplicate-area regressions
# ---------------------------------------------------------------------------


class TestDuplicateRegressions:
    """Regression tests for duplicate area entity creation.

    Two distinct root causes:
    1. Race condition — both map.area and area_name_list transiently empty
       during a map refresh causes all tracked areas to be placed in old_areas
       and removed from the HA entity registry.  When data is restored the
       areas are recreated as genuinely-new entities.
    2. Pymammotion lowercase fallback names ("area 1", "area 2") are treated
       as real device-provided names.  The area counter check uses
       ``name.startswith("Area ")`` (capital A), so "area 1" is invisible to
       the counter.  When a new unnamed area arrives the counter resets to 0
       and generates "Area 1" — but "Area 1" (capital) is not in
       area_entities_by_name (which holds "area 1" lowercase), so a duplicate
       entity is created alongside the existing one.
    """

    def test_empty_map_does_not_remove_tracked_areas(self) -> None:
        """When map.area and area_name_list are both transiently empty,
        no tracked areas should be removed.

        Regression: all_current_areas = {} → old_areas = added_areas → every
        entity is deleted from the HA registry on a transient map refresh.
        """
        h1, h2 = 100, 200
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        # Initial setup — both areas tracked as "Area 1" / "Area 2"
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert added_areas == {h1, h2}

        # Simulate transient wipe: map refresh clears map.area and a concurrent
        # empty toapp_all_hashname clears area_name_list.
        coord.data.map.area = {}
        coord.data.map.area_name = []

        with patch.object(
            _switch_mod, "async_remove_stale_area_entities"
        ) as mock_remove:
            async_add_area_entities(coord, added_areas, by_name, added.extend)
            mock_remove.assert_not_called()

        assert added_areas == {h1, h2}, (
            "Tracked areas must not be removed on transient wipe"
        )
        assert len(added) == 2, "No new entities must be created"

    def test_lowercase_pymammotion_names_no_duplicate(self) -> None:
        """Pymammotion-generated lowercase 'area N' names must not cause a
        duplicate entity when a subsequent unnamed area triggers auto-numbering.

        Regression: the counter check used ``name.startswith("Area ")`` (capital
        A), so lowercase "area 1" / "area 2" were invisible to it.  The counter
        reset to 0 and the new area received "Area 1", which is a different key
        than "area 1" in area_entities_by_name — producing two separate entities
        for logically the same slot.
        """
        h1, h2, h3 = 10, 20, 30

        # Pymammotion sends lowercase fallback names
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("area 1", h1), _area_name("area 2", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert len(added) == 2

        # A new area h3 arrives with no entry in area_name (truly unnamed)
        coord.data.map.area = {
            str(h1): MagicMock(),
            str(h2): MagicMock(),
            str(h3): MagicMock(),
        }
        coord.data.map.area_name = [_area_name("area 1", h1), _area_name("area 2", h2)]

        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 3, "Exactly one new entity must be created for h3"
        assert added_areas == {h1, h2, h3}

        # h3's auto-name must not collide with either existing "area 1" / "area 2"
        h3_name = next(n for n, e in by_name.items() if e.area == h3)
        assert h3_name.lower() != "area 1", (
            f"h3 must not be named 'Area 1' (conflicts with existing 'area 1'); got {h3_name!r}"
        )
        assert h3_name.lower() != "area 2", (
            f"h3 must not be named 'Area 2' (conflicts with existing 'area 2'); got {h3_name!r}"
        )


# ---------------------------------------------------------------------------
# Tests: "Pool" area duplication regression
# ---------------------------------------------------------------------------


class TestPoolAreaNoDuplication:
    """Regression tests for the specific 'Pool' area duplication bug.

    A user reported that after the device assigned a new hash to their 'Pool'
    area, a second 'Area Pool' switch appeared.  The root cause: when the
    device rebuilds an area (same name, new hash) the new hash appears in
    new_areas and must be matched by name to the existing entity so that
    update_area() is called rather than a second entity being created.

    These tests assert the invariant: one area name → one entity, even across
    multiple hash changes and regardless of whether the entity was originally
    unnamed or directly named.
    """

    def _setup_pool(self, h_initial: int):
        """Return (coord, added_areas, by_name, added_list) after one Pool entity is set up."""
        coord = _make_coordinator(
            area_dict={str(h_initial): MagicMock()},
            area_name_list=[_area_name("Pool", h_initial)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert len(added) == 1
        assert "Pool" in by_name
        # Simulate entity being registered with HA
        by_name["Pool"].hass = MagicMock()
        return coord, added_areas, by_name, added

    def test_pool_hash_change_no_duplicate_entity(self) -> None:
        """Pool/H1 → Pool/H2: exactly one entity exists after the update."""
        h1, h2 = 111_000, 222_000
        coord, added_areas, by_name, added = self._setup_pool(h1)

        coord.data.map.area = {str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("Pool", h2)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 1, (
            "No new entity must be created when Pool gets a new hash — "
            f"got {len(added)} entities in added list"
        )
        assert len(by_name) == 1, (
            f"by_name must have exactly one entry; got {list(by_name.keys())}"
        )
        assert "Pool" in by_name

    def test_pool_hash_change_entity_updated_in_place(self) -> None:
        """The entity object must be the same Python object after a hash change."""
        h1, h2 = 111_000, 222_000
        coord, added_areas, by_name, added = self._setup_pool(h1)
        entity_before = by_name["Pool"]

        coord.data.map.area = {str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("Pool", h2)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert by_name["Pool"] is entity_before, (
            "update must reuse the existing entity object, not create a new one"
        )

    def test_pool_hash_change_entity_hash_correct(self) -> None:
        """entity.area and extra_state_attributes must reflect the new hash."""
        h1, h2 = 111_000, 222_000
        coord, added_areas, by_name, added = self._setup_pool(h1)

        coord.data.map.area = {str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("Pool", h2)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        entity = by_name["Pool"]
        assert entity.area == h2, f"entity.area must be {h2}, got {entity.area}"
        assert entity._attr_extra_state_attributes == {"hash": h2}

    def test_pool_hash_change_added_areas_correct(self) -> None:
        """added_areas must contain only the new hash after the update."""
        h1, h2 = 111_000, 222_000
        coord, added_areas, by_name, added = self._setup_pool(h1)

        coord.data.map.area = {str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("Pool", h2)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert h2 in added_areas, "new hash must be in added_areas"
        assert h1 not in added_areas, "old hash must have been discarded"

    def test_pool_multiple_sequential_hash_changes_no_accumulation(self) -> None:
        """Three sequential hash changes (H1→H2→H3) must still leave one entity."""
        h1, h2, h3 = 111_000, 222_000, 333_000
        coord, added_areas, by_name, added = self._setup_pool(h1)

        for new_h in (h2, h3):
            coord.data.map.area = {str(new_h): MagicMock()}
            coord.data.map.area_name = [_area_name("Pool", new_h)]
            async_add_area_entities(coord, added_areas, by_name, added.extend)
            by_name["Pool"].hass = MagicMock()  # keep hass set after each update

        assert len(added) == 1, "Three hash changes must still yield one entity total"
        assert len(by_name) == 1
        assert by_name["Pool"].area == h3

    def test_pool_full_lifecycle_unnamed_then_named_then_rehashed(self) -> None:
        """Full lifecycle: area starts unnamed → device assigns 'Pool' → new hash.

        This reproduces the most common user path:
        1. Area arrives with no device name → auto-assigned 'Area 1'
        2. Device sends real name 'Pool' → entity renamed in place
        3. Device rebuilds area with new hash, still named 'Pool' → entity updated

        At no point should more than one entity exist for this area.
        """
        h1, h2 = 111_000, 222_000

        # Step 1: area arrives unnamed
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock()},
            area_name_list=[_area_name("", h1)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert len(added) == 1
        assert "Area 1" in by_name

        # Step 2: device assigns real name 'Pool'
        coord.data.map.area_name = [_area_name("Pool", h1)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        assert "Pool" in by_name, "area must be renamed to 'Pool'"
        assert "Area 1" not in by_name, "old auto-name must be removed"
        assert len(by_name) == 1
        assert len(added) == 1

        # Step 3: device rebuilds with new hash, still named 'Pool'
        by_name["Pool"].hass = MagicMock()
        coord.data.map.area = {str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("Pool", h2)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 1, (
            "After hash change following rename, still exactly one entity must exist"
        )
        assert len(by_name) == 1, (
            f"by_name must have one entry; got {list(by_name.keys())}"
        )
        assert "Pool" in by_name
        assert by_name["Pool"].area == h2, "entity must hold the new hash"
        assert h2 in added_areas
        assert h1 not in added_areas

    def test_pool_second_call_same_data_no_duplicate(self) -> None:
        """Calling async_add_area_entities twice with identical Pool data must be idempotent."""
        h1 = 111_000
        coord, added_areas, by_name, added = self._setup_pool(h1)

        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(added) == 1, (
            "Repeated call with same data must not create a duplicate"
        )
        assert len(by_name) == 1

    def test_pool_hash_change_write_ha_state_called(self) -> None:
        """async_write_ha_state must be called on the Pool entity when hass is set."""
        h1, h2 = 111_000, 222_000
        coord, added_areas, by_name, added = self._setup_pool(h1)
        entity = by_name["Pool"]
        entity.async_write_ha_state = MagicMock()

        coord.data.map.area = {str(h2): MagicMock()}
        coord.data.map.area_name = [_area_name("Pool", h2)]
        async_add_area_entities(coord, added_areas, by_name, added.extend)

        entity.async_write_ha_state.assert_called()


# ---------------------------------------------------------------------------
# Tests: empty area names are displayed as "Area N" (not "Zone N")
# ---------------------------------------------------------------------------


class TestEmptyAreaNameDisplayedAsAreaN:
    """When area names are empty strings, entities must be labelled 'Area N'.

    Regression guard: the prefix was temporarily 'Zone' during a refactor
    of GeojsonGenerator._NAME_TEMPLATES.  The correct prefix is 'Area' to
    match what pymammotion's HashList.computed_areas generates for unnamed areas.
    """

    def test_empty_names_produce_area_n_entities(self) -> None:
        """Two areas with name='' must create entities keyed 'Area 1' and 'Area 2'."""
        h1, h2 = 100, 200
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert set(by_name.keys()) == {"Area 1", "Area 2"}, (
            f"Expected {{'Area 1', 'Area 2'}} but got {set(by_name.keys())}; "
            "empty names must fall back to 'Area N', not 'Zone N'"
        )
        assert len(added) == 2

    def test_empty_names_never_produce_zone_n(self) -> None:
        """Fallback names must never begin with 'Zone'."""
        h1, h2, h3 = 10, 20, 30
        coord = _make_coordinator(
            area_dict={str(h): MagicMock() for h in (h1, h2, h3)},
            area_name_list=[_area_name("", h) for h in (h1, h2, h3)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}

        async_add_area_entities(coord, added_areas, by_name, [].extend)

        for name in by_name:
            assert not name.lower().startswith("zone"), (
                f"Area name {name!r} must not use the 'Zone' prefix; expected 'Area N'"
            )

    def test_mixed_named_and_unnamed_area_n_for_unnamed_only(self) -> None:
        """Named areas keep their device name; only the unnamed one gets 'Area N'."""
        h_named, h_unnamed = 100, 200
        coord = _make_coordinator(
            area_dict={str(h_named): MagicMock(), str(h_unnamed): MagicMock()},
            area_name_list=[
                _area_name("Front Lawn", h_named),
                _area_name("", h_unnamed),
            ],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert "Front Lawn" in by_name, "Named area must keep its device-assigned name"
        assert by_name["Front Lawn"].area == h_named
        assert any(n.startswith("Area ") for n in by_name if n != "Front Lawn"), (
            "Unnamed area must receive an 'Area N' fallback name"
        )
        assert not any(n.lower().startswith("zone") for n in by_name), (
            "No entity must use the 'Zone' prefix"
        )
        assert len(added) == 2


# ---------------------------------------------------------------------------
# Tests: async_get_area_list trigger in async_add_area_entities
# ---------------------------------------------------------------------------


class TestAreaListFetchTrigger:
    """async_get_area_list must be scheduled when any area hash from map.area
    has no corresponding entry in area_name_list.  For Luba 1 devices it must
    never be triggered (Luba 1 does not provide area_name data)."""

    def test_missing_name_entry_triggers_fetch(self) -> None:
        """When a hash is present in map.area but absent from area_name_list,
        async_create_task must be called once to schedule async_get_area_list."""
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("front", h1)],  # h2 has no name entry
        )
        added_areas: set[int] = set()
        by_name: dict = {}

        async_add_area_entities(coord, added_areas, by_name, [].extend)

        coord.hass.async_create_task.assert_called_once()

    def test_all_names_present_no_fetch(self) -> None:
        """When every hash in map.area has a matching area_name entry, no fetch
        must be scheduled."""
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("front", h1), _area_name("back", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}

        async_add_area_entities(coord, added_areas, by_name, [].extend)

        coord.hass.async_create_task.assert_not_called()

    def test_luba1_never_triggers_fetch(self) -> None:
        """Luba 1 does not send area_name data — the fetch gate must be skipped
        entirely regardless of how many names are missing."""
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[],  # all names absent
            is_luba1=True,
        )
        added_areas: set[int] = set()
        by_name: dict = {}

        async_add_area_entities(coord, added_areas, by_name, [].extend)

        coord.hass.async_create_task.assert_not_called()

    def test_empty_area_dict_no_fetch(self) -> None:
        """Empty map.area means there are no hashes to fetch names for."""
        coord = _make_coordinator(area_dict={}, area_name_list=[])
        added_areas: set[int] = set()
        by_name: dict = {}

        async_add_area_entities(coord, added_areas, by_name, [].extend)

        coord.hass.async_create_task.assert_not_called()

    def test_all_empty_names_no_fetch_when_hashes_covered(self) -> None:
        """Having empty-string names is not the same as missing entries — if every
        hash has an area_name_list entry (even with name=''), no fetch is needed."""
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("", h1), _area_name("", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}

        async_add_area_entities(coord, added_areas, by_name, [].extend)

        coord.hass.async_create_task.assert_not_called()


class TestEarlyReturnOnNoChange:
    """async_add_area_entities must skip all work when hashes and names are unchanged."""

    def test_same_hashes_same_names_no_entities_added(self) -> None:
        """Second call with identical data must not add any entities."""
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("Front", h1), _area_name("Back", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        first_added: list = []
        async_add_area_entities(coord, added_areas, by_name, first_added.extend)
        assert len(first_added) == 2

        # Second call — same data
        second_added: list = []
        async_add_area_entities(coord, added_areas, by_name, second_added.extend)
        assert len(second_added) == 0

    def test_same_hashes_same_names_entities_by_name_unchanged(self) -> None:
        """Second call with identical data must not modify area_entities_by_name."""
        h = 555
        coord = _make_coordinator(
            area_dict={str(h): MagicMock()},
            area_name_list=[_area_name("Garden", h)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)
        snapshot = dict(by_name)

        async_add_area_entities(coord, added_areas, by_name, [].extend)
        assert by_name == snapshot

    def test_name_change_is_not_skipped(self) -> None:
        """When a name changes the early-return must not fire — entity is updated."""
        h = 777
        coord = _make_coordinator(
            area_dict={str(h): MagicMock()},
            area_name_list=[_area_name("Old Name", h)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []
        async_add_area_entities(coord, added_areas, by_name, added.extend)
        entity = added[0]
        entity.update_name = MagicMock()

        # Rename on the device
        coord.data.map.area_name = [_area_name("New Name", h)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        entity.update_name.assert_called_once_with("New Name")

    def test_new_hash_is_not_skipped(self) -> None:
        """When a new area hash appears the early-return must not fire."""
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock()},
            area_name_list=[_area_name("Front", h1)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)

        # Second area appears
        coord.data.map.area[str(h2)] = MagicMock()
        coord.data.map.area_name.append(_area_name("Back", h2))
        second_added: list = []
        async_add_area_entities(coord, added_areas, by_name, second_added.extend)
        assert len(second_added) == 1
        assert second_added[0].area == h2

    def test_removed_hash_is_not_skipped(self) -> None:
        """When an area is removed the early-return must not fire."""
        h1, h2 = 111, 222
        coord = _make_coordinator(
            area_dict={str(h1): MagicMock(), str(h2): MagicMock()},
            area_name_list=[_area_name("Front", h1), _area_name("Back", h2)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        async_add_area_entities(coord, added_areas, by_name, [].extend)
        assert added_areas == {h1, h2}

        # Remove h2
        del coord.data.map.area[str(h2)]
        coord.data.map.area_name = [_area_name("Front", h1)]
        async_add_area_entities(coord, added_areas, by_name, [].extend)
        assert h2 not in added_areas


# ---------------------------------------------------------------------------
# Tests: restart reconciliation against the entity registry
# ---------------------------------------------------------------------------


class _RegistryEntry:
    """Stand-in for homeassistant.helpers.entity_registry.RegistryEntry."""

    def __init__(
        self,
        entity_id: str,
        unique_id: str,
        original_name: str | None = None,
        name: str | None = None,
        domain: str = "switch",
        platform: str = "mammotion",
        translation_key: str | None = None,
    ) -> None:
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.original_name = original_name
        self.name = name
        self.domain = domain
        self.platform = platform
        self.translation_key = translation_key


class _FakeRegistry:
    """Registry fake tracking re-keys and removals."""

    def __init__(self, entries: list[_RegistryEntry]) -> None:
        self.entities = {e.entity_id: e for e in entries}
        self.updated: list[tuple[str, str]] = []
        self.removed: list[str] = []

    def async_get_entity_id(self, domain, platform, unique_id):
        for entry in self.entities.values():
            if entry.unique_id == unique_id:
                return entry.entity_id
        return None

    def async_update_entity(self, entity_id, new_unique_id=None, **kwargs):
        self.entities[entity_id].unique_id = new_unique_id
        self.updated.append((entity_id, new_unique_id))

    def async_remove(self, entity_id):
        self.entities.pop(entity_id, None)
        self.removed.append(entity_id)


class TestUniqueIdRekeyOnHashChange:
    """update_area must persist the new hash, or the next restart duplicates."""

    def _entity(self, coord, area_id: int):
        description = _switch_mod.MammotionConfigAreaSwitchEntityDescription(
            key=f"{area_id}",
            translation_key="area",
            area=area_id,
            name="Front lawn",
            set_fn=lambda c, v, a: None,
        )
        entity = _switch_mod.MammotionConfigAreaSwitchEntity(coord, description)
        entity.hass = None
        return entity

    def test_unregistered_entity_updates_attr_only(self) -> None:
        coord = _make_coordinator(area_dict={}, area_name_list=[])
        entity = self._entity(coord, 111)
        entity._attr_unique_id = "Luba-TEST_111"

        entity.update_area(222)

        assert entity._attr_unique_id == "Luba-TEST_222"

    def test_registered_entity_rekeys_registry(self) -> None:
        coord = _make_coordinator(area_dict={}, area_name_list=[])
        entity = self._entity(coord, 111)
        entity._attr_unique_id = "Luba-TEST_111"
        entity.hass = MagicMock()
        entity.registry_entry = _RegistryEntry(
            "switch.luba_test_front_lawn", "Luba-TEST_111"
        )
        registry = _FakeRegistry([entity.registry_entry])

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            entity.update_area(222)

        assert registry.updated == [("switch.luba_test_front_lawn", "Luba-TEST_222")]

    def test_collision_leaves_registry_untouched(self) -> None:
        """If the new hash is already registered, do not raise over it."""
        coord = _make_coordinator(area_dict={}, area_name_list=[])
        entity = self._entity(coord, 111)
        entity._attr_unique_id = "Luba-TEST_111"
        entity.hass = MagicMock()
        entity.registry_entry = _RegistryEntry(
            "switch.luba_test_front_lawn", "Luba-TEST_111"
        )
        registry = _FakeRegistry(
            [
                entity.registry_entry,
                _RegistryEntry("switch.luba_test_other", "Luba-TEST_222"),
            ]
        )

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            entity.update_area(222)

        assert registry.updated == []
        assert entity.area == 222


# ---------------------------------------------------------------------------
# Tests: disable/re-enable must not duplicate area switches
# ---------------------------------------------------------------------------


class TestReloadDoesNotDuplicateEntities:
    """Reloading the integration must reuse existing registry entries.

    After a disable/re-enable (or HA restart), the in-memory tracking state
    (``added_areas`` / ``area_entities_by_name``) starts empty, so the
    name-based reconciliation inside async_add_area_entities never sees the
    previous session's entities.  When the device hands out new hashes for the
    same named areas, a brand-new unique_id is minted while the entity registry
    still holds the previous entry (e.g. unique_id
    "Luba-VAME9R5S_1079026611409949868" for "Area Baksida") — HA then registers
    a second entity and, because the friendly entity_id is taken, appends a
    "_2" suffix (switch.garden_luba_vame9r5s_area_baksida_2).  Each further
    cycle bumps the suffix again.

    The existing registry entry must instead be re-keyed to the new hash so the
    original entity_id, history, and user customisations survive the reload.
    """

    def _fresh_session(
        self, name: str, new_hash: int
    ) -> tuple[MagicMock, set[int], dict, list]:
        """Coordinator + empty tracking state, as after a reload."""
        coord = _make_coordinator(
            area_dict={str(new_hash): MagicMock()},
            area_name_list=[_area_name(name, new_hash)],
        )
        return coord, set(), {}, []

    @staticmethod
    def _stale_baksida_registry(old_hash: int) -> _FakeRegistry:
        """Registry holding the previous session's entry for area 'Baksida'."""
        return _FakeRegistry(
            [
                _RegistryEntry(
                    "switch.garden_luba_test_area_baksida",
                    f"Luba-TEST_{old_hash}",
                    original_name="Area Baksida",
                    translation_key="area",
                )
            ]
        )

    def test_named_area_new_hash_rekeys_registry_entry(self) -> None:
        """A prior-session registry entry for the same named area must be
        re-keyed to the new hash instead of a duplicate being created."""
        old_h, new_h = 1079026611409949868, 373475585020721672
        coord, added_areas, by_name, added = self._fresh_session("Baksida", new_h)
        registry = self._stale_baksida_registry(old_h)

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert registry.updated == [
            ("switch.garden_luba_test_area_baksida", f"Luba-TEST_{new_h}")
        ], "Stale registry entry must be re-keyed to the new hash on reload"

    def test_named_area_new_hash_leaves_no_stale_unique_id(self) -> None:
        """After the reload reconciliation, no registry entry may still carry
        the previous session's unique_id — that stale entry is what forces HA
        to suffix the new entity's entity_id with _2."""
        old_h, new_h = 1079026611409949868, 373475585020721672
        coord, added_areas, by_name, added = self._fresh_session("Baksida", new_h)
        registry = self._stale_baksida_registry(old_h)

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert (
            registry.async_get_entity_id("switch", "mammotion", f"Luba-TEST_{old_h}")
            is None
        ), "Previous session's unique_id must not survive the reload"

    def test_auto_named_area_new_hash_rekeys_registry_entry(self) -> None:
        """Auto-named areas ('Area 1', 'Area 2', …) duplicate the same way —
        switch.garden_luba_vame9r5s_area_area_1 became ..._area_1_2."""
        old_h, new_h = 6630234128293022052, 865714081496397399
        # Device sends no name → computed_areas auto-names it "Area 1".
        coord = _make_coordinator(
            area_dict={str(new_h): MagicMock()},
            area_name_list=[_area_name("", new_h)],
        )
        added_areas: set[int] = set()
        by_name: dict = {}
        added: list = []

        stale = _RegistryEntry(
            "switch.garden_luba_test_area_area_1",
            f"Luba-TEST_{old_h}",
            original_name="Area Area 1",
            translation_key="area",
        )
        registry = _FakeRegistry([stale])

        with patch.object(_switch_mod.er, "async_get", return_value=registry):
            async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert registry.updated == [
            ("switch.garden_luba_test_area_area_1", f"Luba-TEST_{new_h}")
        ], "Auto-named area's registry entry must be re-keyed on reload"

    def test_repeated_reload_cycles_do_not_accumulate_entries(self) -> None:
        """Two disable/re-enable cycles, a new hash each time (as in the field
        report), must keep exactly one registry entry for the area."""
        h_session1, h_session2, h_session3 = 111, 222, 333

        registry = self._stale_baksida_registry(h_session1)

        for new_hash in (h_session2, h_session3):
            coord, added_areas, by_name, added = self._fresh_session(
                "Baksida", new_hash
            )
            with patch.object(_switch_mod.er, "async_get", return_value=registry):
                async_add_area_entities(coord, added_areas, by_name, added.extend)

        assert len(registry.entities) == 1, (
            "Reload cycles must never grow the registry for the same area"
        )
        (entry,) = registry.entities.values()
        assert entry.entity_id == "switch.garden_luba_test_area_baksida", (
            "The original entity_id must survive every reload"
        )
        assert entry.unique_id == f"Luba-TEST_{h_session3}"
