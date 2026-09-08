"""Tests for MammotionReportUpdateCoordinator.get_area_entity_name.

Specifically covers the 'Area N' fallback for areas whose device-assigned name
is an empty string — the fix for the earlier bug where empty names produced an
unreadable 'area 1451834635207421727' string instead of a readable 'Area N'.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from enum import IntEnum
from pathlib import Path
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Extend the stubs from conftest.py with the extra imports coordinator.py
# needs that switch.py does not.  conftest.py has already run, so we add
# missing attrs to already-registered stubs and create new ones for modules
# that conftest.py did not stub at all.
# ---------------------------------------------------------------------------

def _extend_stub(name: str, **attrs) -> types.ModuleType:
    """Add attributes to an existing stub, or create a new one."""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    for k, v in attrs.items():
        setattr(sys.modules[name], k, v)
    return sys.modules[name]


# DataUpdateCoordinator must support PEP 695 subscript syntax [DataT]
class _SubscriptableBase:
    def __class_getitem__(cls, item):
        return cls

_extend_stub(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=_SubscriptableBase,
    CoordinatorEntity=object,
)

# habluetooth
_extend_stub("habluetooth", BluetoothScanningMode=MagicMock())
_extend_stub("habluetooth.models", BluetoothServiceInfoBleak=MagicMock())

# homeassistant additions
_extend_stub("homeassistant.helpers.debounce", Debouncer=MagicMock())

# mashumaro
_extend_stub("mashumaro.exceptions", InvalidFieldValue=Exception)
_extend_stub("mashumaro")

# pymammotion.aliyun.exceptions — add to existing stub
_extend_stub(
    "pymammotion.aliyun.exceptions",
    DeviceOfflineException=Exception,
    FailedRequestException=Exception,
    GatewayTimeoutException=Exception,
    TooManyRequestsException=Exception,
    CheckSessionException=Exception,
    CloudSetupError=Exception,
)

# pymammotion.data.model — top-level package
_extend_stub("pymammotion.data.model", GenerateRouteInformation=MagicMock())

# pymammotion.data.model.device — extend with classes not in conftest
_extend_stub(
    "pymammotion.data.model.device",
    MowerDevice=MagicMock(),
    MowingDevice=MagicMock(),
    MowerInfo=MagicMock(),
    RTKDevice=MagicMock(),
    RTKBaseStationDevice=MagicMock(),
    PoolCleanerDevice=MagicMock(),
)

# pymammotion.data.model.device_config
_extend_stub(
    "pymammotion.data.model.device_config",
    OperationSettings=MagicMock(),
    create_path_order=MagicMock(),
)

# pymammotion.data.model.hash_list — extend with Plan, SvgMessage
_extend_stub("pymammotion.data.model.hash_list", Plan=MagicMock(), SvgMessage=MagicMock())

# pymammotion.data.model.pool_state — extend with PoolPlan
_extend_stub("pymammotion.data.model.pool_state", PoolPlan=MagicMock(), SpinoToggle=MagicMock())

# pymammotion.data.model.report_info
_extend_stub("pymammotion.data.model.report_info", Maintain=MagicMock(), NetUsedType=MagicMock())

# pymammotion.data.mqtt.*
_extend_stub("pymammotion.data")
_extend_stub("pymammotion.data.mqtt")
_extend_stub(
    "pymammotion.data.mqtt.event",
    DeviceNotificationEventParams=MagicMock(),
    ThingEventMessage=MagicMock(),
)
_extend_stub("pymammotion.data.mqtt.properties", ThingPropertiesMessage=MagicMock())
_extend_stub(
    "pymammotion.data.mqtt.status",
    StatusType=MagicMock(),
    ThingStatusMessage=MagicMock(),
)

# pymammotion.http.model.http — extend
_extend_stub("pymammotion.http.model.http", ErrorInfo=MagicMock(), Response=MagicMock(), UnauthorizedException=Exception, UnauthorizedExceptionError=Exception)

# pymammotion.mammotion.commands.mammotion_command
_extend_stub("pymammotion.mammotion.commands")
_extend_stub("pymammotion.mammotion.commands.mammotion_command", MammotionCommand=MagicMock())


# pymammotion.messaging.command_queue — a real enum, not a MagicMock.  coordinator.py
# branches on ``priority.is_direct``, and every attribute of a MagicMock is truthy,
# so a stubbed Priority would make every command look user-initiated.  Mirrors
# pymammotion/messaging/command_queue.py.
class _Priority(IntEnum):
    EMERGENCY = 0
    USER = 1
    EXCLUSIVE = 2
    NORMAL = 3
    BACKGROUND = 4

    @property
    def is_direct(self) -> bool:
        return self <= _Priority.USER


_extend_stub("pymammotion.messaging")
_extend_stub("pymammotion.messaging.command_queue", Priority=_Priority)

# pymammotion.proto
_extend_stub("pymammotion.proto", MulSex=MagicMock())

# pymammotion.state.*
_extend_stub("pymammotion.state")
_extend_stub(
    "pymammotion.state.device_state",
    DeviceShutdownEvent=MagicMock(),
    DeviceSnapshot=MagicMock(),
)

# pymammotion.transport.ble
_extend_stub("pymammotion.transport.ble", BLETransport=MagicMock())

# pymammotion.utility.constant — add MOWING_ACTIVE_MODES
_extend_stub("pymammotion.utility.constant", WorkMode=MagicMock(), MOWING_ACTIVE_MODES=MagicMock())

# pymammotion.utility.plan_id / svg
_extend_stub("pymammotion.utility.plan_id", make_copy_name=MagicMock(), new_mower_plan_id=MagicMock())
_extend_stub("pymammotion.utility.svg", chunk_svg_messages=MagicMock())

# webrtc_models — extend
_extend_stub("webrtc_models", RTCIceCandidateInit=object, RTCIceServer=object)

# Local mammotion submodules that coordinator.py imports
_extend_stub(
    "custom_components.mammotion.agora_api",
    SERVICE_IDS=MagicMock(),
    AgoraAPIClient=MagicMock(),
    AgoraResponse=MagicMock(),
)
_extend_stub(
    "custom_components.mammotion.config",
    MammotionConfigStore=MagicMock(),
    async_get_store=MagicMock(),
    TRANSPORT_BLUETOOTH="bluetooth_enabled",
    TRANSPORT_CLOUD="cloud_enabled",
)

# ---------------------------------------------------------------------------
# Load coordinator.py
# ---------------------------------------------------------------------------

_FAKE_PKG = "_test_mammotion_coord_pkg"

# The fake parent package — coordinator.py's relative imports (.config, .const, .agora_api)
# resolve against __package__, which we'll set to _FAKE_PKG.  By routing through a
# completely isolated name we avoid triggering a real-fs load of custom_components.mammotion.
_fake_pkg = types.ModuleType(_FAKE_PKG)
_fake_pkg.__path__ = []
sys.modules[_FAKE_PKG] = _fake_pkg

# Mirror the already-stubbed custom_components.mammotion.* entries under the fake package.
for _suffix in ("const", "config", "agora_api"):
    _real_key = f"custom_components.mammotion.{_suffix}"
    _fake_key = f"{_FAKE_PKG}.{_suffix}"
    if _real_key in sys.modules:
        sys.modules[_fake_key] = sys.modules[_real_key]
    else:
        sys.modules[_fake_key] = types.ModuleType(_fake_key)

_coord_path = Path(__file__).parent.parent / "custom_components" / "mammotion" / "coordinator.py"
_coord_spec = importlib.util.spec_from_file_location("_test_coord_module", _coord_path)
_coord_mod = importlib.util.module_from_spec(_coord_spec)
_coord_mod.__package__ = _FAKE_PKG
sys.modules["_test_coord_module"] = _coord_mod
_coord_spec.loader.exec_module(_coord_mod)

_MammotionReportUpdateCoordinator = _coord_mod.MammotionReportUpdateCoordinator


# ---------------------------------------------------------------------------
# Minimal test coordinator
# ---------------------------------------------------------------------------


class _AreaFrame:
    """Minimal frame stub with just a .hash attribute."""
    def __init__(self, hash_val: int) -> None:
        self.hash = hash_val


class _AreaData:
    """Minimal FrameList stub."""
    def __init__(self, hash_val: int) -> None:
        self.data = [_AreaFrame(hash_val)]
        self.name = ""


class _MapData:
    def __init__(self, area: dict, area_name: list) -> None:
        self.area = area
        self.area_name = area_name

    @property
    def computed_areas(self) -> list:
        """Mirror HashList.computed_areas gap-filling logic for test isolation."""

        class _A:
            __slots__ = ("name", "hash")

            def __init__(self, name: str, hash_val: int) -> None:
                self.name = name
                self.hash = hash_val

        result = [_A(a.name, a.hash) for a in self.area_name]
        by_hash = {a.hash: a for a in result}
        used: set[int] = {
            int(a.name.split()[-1])
            for a in result
            if a.name.lower().startswith("area ") and a.name.split()[-1].isdigit()
        }
        next_n = [1]

        def _take() -> int:
            while next_n[0] in used:
                next_n[0] += 1
            used.add(next_n[0])
            return next_n[0]

        for h, area in self.area.items():
            existing = by_hash.get(h)
            frame_name = getattr(area, "name", "")
            if existing is None:
                entry = _A(frame_name if frame_name else f"Area {_take()}", h)
                result.append(entry)
                by_hash[h] = entry
            elif not existing.name and frame_name:
                existing.name = frame_name
            elif not existing.name:
                existing.name = f"Area {_take()}"

        return result


class _DeviceData:
    def __init__(self, area: dict, area_name: list) -> None:
        self.map = _MapData(area, area_name)


def _area_name(name: str, hash_val: int):
    """Create an AreaHashNameList-like stub."""
    obj = MagicMock()
    obj.name = name
    obj.hash = hash_val
    return obj


def _coord(
    area_dict: dict[int, None],
    area_name_list: list,
    *,
    ha_names: dict[int, str] | None = None,
):
    """Create a minimal coordinator instance (bypasses __init__).

    ha_names maps area_hash → user-set HA entity registry name, for testing
    the registry-lookup path in get_area_entity_name.
    """
    inst = _MammotionReportUpdateCoordinator.__new__(_MammotionReportUpdateCoordinator)
    inst.data = _DeviceData(
        area={h: _AreaData(h) for h in area_dict},
        area_name=area_name_list,
    )
    inst.unique_name = "test-device"
    inst.hass = MagicMock()

    _ha = ha_names or {}
    mock_reg = MagicMock()

    def _get_entity_id(platform, domain, unique_id):
        for h in _ha:
            if unique_id == f"test-device_{h}":
                return f"switch.test_area_{h}"
        return None

    def _get_entry(entity_id):
        if entity_id is None:
            return None
        for h, name in _ha.items():
            if entity_id == f"switch.test_area_{h}":
                entry = MagicMock()
                entry.name = name
                return entry
        return None

    mock_reg.async_get_entity_id = MagicMock(side_effect=_get_entity_id)
    mock_reg.async_get = MagicMock(side_effect=_get_entry)

    er_mod = sys.modules.get("homeassistant.helpers.entity_registry")
    er_mod.async_get = MagicMock(return_value=mock_reg)

    return inst


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetAreaEntityName:
    """get_area_entity_name must return readable names even for empty-string
    area names, using the same 'Area N' fallback as GeojsonGenerator."""

    def test_zero_hash_returns_none(self) -> None:
        """hash == 0 is the sentinel for 'no zone'; must return None."""
        coord = _coord({}, [])
        assert coord.get_area_entity_name(0) is None

    def test_hash_not_in_area_returns_path(self) -> None:
        """A hash that has no area frame must return 'path' (mow-path sentinel)."""
        coord = _coord({}, [])
        assert coord.get_area_entity_name(999) == "path"

    def test_real_name_returned_as_is(self) -> None:
        """When the area has a non-empty device-assigned name it is returned unchanged."""
        h = 111
        coord = _coord({h: None}, [_area_name("Front Lawn", h)])
        assert coord.get_area_entity_name(h) == "Front Lawn"

    def test_empty_name_returns_area_n(self) -> None:
        """An empty name must produce 'Area N' (1-based index in area_name list)."""
        h = 111
        coord = _coord({h: None}, [_area_name("", h)])
        result = coord.get_area_entity_name(h)
        assert result == "Area 1", (
            f"Expected 'Area 1' for the first area with an empty name, got {result!r}"
        )

    def test_empty_name_not_zone_n(self) -> None:
        """The fallback must never use the 'Zone' prefix."""
        h = 111
        coord = _coord({h: None}, [_area_name("", h)])
        result = coord.get_area_entity_name(h)
        assert result is not None
        assert not result.lower().startswith("zone"), (
            f"Expected 'Area N', not a 'Zone' name; got {result!r}"
        )

    def test_two_areas_empty_names_indexed_correctly(self) -> None:
        """With two empty-named areas the indices must be 1 and 2 respectively."""
        h1, h2 = 100, 200
        area_name_list = [_area_name("", h1), _area_name("", h2)]
        coord = _coord({h1: None, h2: None}, area_name_list)

        assert coord.get_area_entity_name(h1) == "Area 1"
        assert coord.get_area_entity_name(h2) == "Area 2"

    def test_index_based_on_area_name_list_position(self) -> None:
        """Index is derived from position in area_name, not hash magnitude."""
        h_large, h_small = 9_999_999, 1
        # area_name_list has the large hash first — it should get index 1
        area_name_list = [_area_name("", h_large), _area_name("", h_small)]
        coord = _coord({h_large: None, h_small: None}, area_name_list)

        assert coord.get_area_entity_name(h_large) == "Area 1"
        assert coord.get_area_entity_name(h_small) == "Area 2"

    def test_empty_name_not_in_area_name_list_fallback(self) -> None:
        """When a hash is in area dict but absent from area_name, computed_areas
        still auto-assigns 'Area 1' rather than exposing the raw hash."""
        h = 12345
        coord = _coord({h: None}, [])  # area_name is empty
        result = coord.get_area_entity_name(h)
        assert result == "Area 1", (
            f"Hash absent from area_name should still show 'Area 1', got {result!r}"
        )

    def test_mixed_named_and_unnamed(self) -> None:
        """Named areas return their name; unnamed ones return 'Area N'.
        The named area does not consume an 'Area N' slot, so the unnamed one
        gets 'Area 1' (gap-filling, not position-based)."""
        h_named, h_unnamed = 10, 20
        area_name_list = [_area_name("Back Garden", h_named), _area_name("", h_unnamed)]
        coord = _coord({h_named: None, h_unnamed: None}, area_name_list)

        assert coord.get_area_entity_name(h_named) == "Back Garden"
        assert coord.get_area_entity_name(h_unnamed) == "Area 1"


class TestGetAreaEntityNameHARegistryOverride:
    """When a user has renamed an area entity in the HA registry, get_area_entity_name
    must return that name so sensors (work_area, task_area) stay in sync."""

    def test_ha_registry_name_overrides_device_name(self) -> None:
        """User-set HA name wins over the device-assigned name."""
        h = 111
        coord = _coord(
            {h: None},
            [_area_name("Front Garden", h)],
            ha_names={h: "My Front Zone"},
        )
        assert coord.get_area_entity_name(h) == "My Front Zone"

    def test_ha_registry_name_overrides_auto_name(self) -> None:
        """User-set HA name wins over the 'Area N' fallback."""
        h = 222
        coord = _coord(
            {h: None},
            [_area_name("", h)],
            ha_names={h: "Side Strip"},
        )
        assert coord.get_area_entity_name(h) == "Side Strip"

    def test_no_ha_registry_name_falls_back_to_computed(self) -> None:
        """When the entity registry has no user name, computed_areas name is used."""
        h = 333
        coord = _coord({h: None}, [_area_name("Back Lawn", h)])
        assert coord.get_area_entity_name(h) == "Back Lawn"

    def test_ha_registry_name_only_for_matching_hash(self) -> None:
        """A user name for one hash must not bleed onto a different hash."""
        h1, h2 = 10, 20
        area_name_list = [_area_name("", h1), _area_name("", h2)]
        coord = _coord(
            {h1: None, h2: None},
            area_name_list,
            ha_names={h1: "My Zone"},
        )
        assert coord.get_area_entity_name(h1) == "My Zone"
        assert coord.get_area_entity_name(h2) == "Area 2"
