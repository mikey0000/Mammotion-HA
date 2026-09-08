"""Stub out heavy Home Assistant / external imports for unit tests.

Runs before any test module is imported so that package-level import chains
don't drag in native-extension modules (bluetooth, usb, aiousbwatcher …).
"""

import sys
import types
from unittest.mock import MagicMock


def _stub(name: str, **attrs) -> types.ModuleType:
    """Create and register a stub module with optional attributes."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# ── exception classes used across multiple modules ────────────────────────
class _LoginFailedError(Exception): pass
class _ReLoginRequiredError(Exception): pass
class _SessionExpiredError(Exception): pass
class _BLEUnavailableError(Exception): pass

# ── third-party stubs ──────────────────────────────────────────────────────
_stub("aiousbwatcher", AIOUSBWatcher=MagicMock(), InotifyNotAvailableError=Exception)
_stub("bluetooth_adapters")
_stub("habluetooth")
_stub("bleak", BleakClient=MagicMock())
_stub("bleak.backends", device=MagicMock())
_stub("bleak.backends.device", BLEDevice=MagicMock())
_stub("bleak.exc", BleakError=Exception)
_stub("bleak_retry_connector", BleakNotFoundError=Exception)

# aiohttp — needs ClientConnectorError as a distinct class so it doesn't shadow other exceptions
class _ClientConnectorError(Exception): pass
_aiohttp = _stub("aiohttp", ClientConnectorError=_ClientConnectorError)
_stub("aiohttp.web_exceptions", HTTPException=Exception)

# ── Home Assistant stubs ───────────────────────────────────────────────────
# homeassistant.core
_core = _stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f, ServiceCall=object, ServiceResponse=object, SupportsResponse=MagicMock(), Event=object, HassJob=object, CALLBACK_TYPE=object)

_stub("homeassistant.config_entries", ConfigEntry=object, ConfigFlow=object, ConfigFlowResult=object, OptionsFlow=object)

_stub("homeassistant.const", CONF_ADDRESS=str, CONF_PASSWORD=str, STATE_ON="on", Platform=MagicMock(), EVENT_HOMEASSISTANT_STOP="homeassistant_stop")

class _ConfigEntryAuthFailed(Exception): pass
class _ConfigEntryNotReady(Exception): pass
class _ConfigEntryError(Exception): pass
class _HomeAssistantError(Exception): pass
_stub("homeassistant.exceptions", ConfigEntryAuthFailed=_ConfigEntryAuthFailed, ConfigEntryNotReady=_ConfigEntryNotReady, ConfigEntryError=_ConfigEntryError, HomeAssistantError=_HomeAssistantError)

from dataclasses import dataclass, field as _field

@dataclass(frozen=True, kw_only=True)
class _SwitchEntityDescription:
    """Minimal stub for SwitchEntityDescription."""
    key: str = ""
    name: str | None = None
    entity_category: object = None
    translation_key: str | None = None
    translation_placeholders: dict | None = None
    device_class: object = None
    icon: str | None = None

class _SwitchEntity:
    """Minimal SwitchEntity stub."""
    def __init__(self, *args, **kwargs): pass

_switch_mod = _stub("homeassistant.components.switch", DOMAIN="switch", SwitchEntity=_SwitchEntity, SwitchEntityDescription=_SwitchEntityDescription)

_stub("homeassistant.components.bluetooth", async_ble_device_from_address=MagicMock(), BluetoothServiceInfo=object, async_discovered_service_info=MagicMock(), BluetoothCallbackMatcher=MagicMock(), BluetoothChange=MagicMock(), BluetoothScanningMode=MagicMock(), BluetoothServiceInfoBleak=MagicMock(), async_register_callback=MagicMock())
_stub("homeassistant.components.camera", Camera=object, CameraEntityDescription=object, WebRTCAnswer=object, WebRTCError=object, WebRTCSendMessage=object, CameraEntityFeature=MagicMock())
_stub("homeassistant.components.web_rtc", async_register_ice_servers=MagicMock())

_er_mod = _stub("homeassistant.helpers.entity_registry", async_get=MagicMock())
_stub("homeassistant.helpers.entity", EntityCategory=MagicMock())
_stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
class _RestoreEntity:
    """Minimal RestoreEntity stub."""
    def __init__(self, *args, **kwargs): pass
_stub("homeassistant.helpers.restore_state", RestoreEntity=_RestoreEntity)
_stub("homeassistant.helpers.update_coordinator", CoordinatorEntity=object, DataUpdateCoordinator=object)
_stub("homeassistant.helpers.device_registry", CONNECTION_BLUETOOTH="bluetooth", CONNECTION_NETWORK_MAC="network_mac", DeviceInfo=MagicMock(), format_mac=lambda m: m, DeviceEntry=MagicMock(), async_get=MagicMock())
_helpers_mod = _stub("homeassistant.helpers", aiohttp_client=MagicMock(), config_validation=MagicMock())
_helpers_mod.__path__ = []  # mark as package so submodule imports resolve
_stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=MagicMock())
_stub("homeassistant.helpers.event", async_call_later=MagicMock(), async_track_time_interval=MagicMock())
_stub("homeassistant.helpers.storage", Store=MagicMock())
_stub("homeassistant.loader", async_get_integration=MagicMock())

_stub("webrtc_models", RTCIceCandidateInit=object, RTCIceServer=object)

# ── pymammotion stubs (only what switch.py uses) ──────────────────────────
_stub("pymammotion")
_stub("pymammotion.data")
_stub("pymammotion.data.model")

# AreaHashNameList is referenced in switch.py at import time via annotation.
# Provide a real dataclass-like stub.
class _AreaHashNameList:
    def __init__(self, name: str, hash: int) -> None:  # noqa: A002
        self.name = name
        self.hash = hash

_hash_list_mod = _stub("pymammotion.data.model.hash_list", AreaHashNameList=_AreaHashNameList)

_stub("pymammotion.data.model.device", MowingDevice=MagicMock(), RTKDevice=MagicMock(), PoolCleanerDevice=MagicMock())
_stub("pymammotion.data.model.pool_state", SpinoToggle=MagicMock())
_stub("pymammotion.utility")
_stub("pymammotion.utility.device_type", DeviceType=MagicMock())
_stub("pymammotion.utility.constant", WorkMode=MagicMock())
_stub("pymammotion.aliyun")
_stub(
    "pymammotion.aliyun.cloud_gateway",
    CheckSessionException=Exception,
    SetupException=Exception,
    CloudSetupError=Exception,
)
_stub("pymammotion.aliyun.exceptions", TooManyRequestsException=Exception, CheckSessionException=Exception)
_stub("pymammotion.aliyun.model")
_stub("pymammotion.aliyun.model.dev_by_account_response", Device=MagicMock())
_stub("pymammotion.http")
_stub("pymammotion.http.model")
_stub("pymammotion.http.model.http", UnauthorizedException=Exception, UnauthorizedExceptionError=Exception)
_stub("Tea")
_stub("Tea.exceptions", UnretryableException=Exception)
_stub("pymammotion.data.model.account", Credentials=MagicMock())
_stub("pymammotion.http.model.camera_stream", StreamSubscriptionResponse=MagicMock())
_stub("pymammotion.mammotion")
_stub("pymammotion.mammotion.devices")
_stub("pymammotion.mammotion.devices.mammotion_bluetooth", CharacteristicMissingError=Exception)
_stub("pymammotion.transport")
_stub(
    "pymammotion.transport.base",
    NoTransportAvailableError=Exception,
    LoginFailedError=_LoginFailedError,
    ReLoginRequiredError=_ReLoginRequiredError,
    SessionExpiredError=_SessionExpiredError,
    BLEUnavailableError=_BLEUnavailableError,
    AuthError=Exception,
    TransportType=MagicMock(),
    Subscription=MagicMock(),
    CommandTimeoutError=Exception,
    ConcurrentRequestError=Exception,
    SagaFailedError=Exception,
    TransportRateLimitedError=Exception,
    TransportError=Exception,
    AccountInUseError=Exception,
    is_transient_network_error=MagicMock(return_value=False),
)
_stub("pymammotion.client", MammotionClient=MagicMock())

# ── mammotion package itself ───────────────────────────────────────────────
# Stub __init__.py so relative imports in switch.py work without running it.
_mammotion_pkg = _stub("custom_components")
_mammotion_mammotion = _stub("custom_components.mammotion", MammotionConfigEntry=object)
_mammotion_mammotion.__path__ = []  # mark as package

_const_mod = _stub(
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
    CONF_HAS_CLOUD_ACCOUNT="has_cloud_account",
    CONF_AEP_DATA="aep_data",
    CONF_AUTH_DATA="auth_data",
    CONF_REGION_DATA="region_data",
    CONF_SESSION_DATA="session_data",
    CONF_DEVICE_DATA="device_data",
    CONF_CONNECT_DATA="connect_data",
    CONF_MAMMOTION_DATA="mammotion_data",
    CONF_MAMMOTION_DEVICE_LIST="mammotion_device_list",
    CONF_MAMMOTION_DEVICE_RECORDS="mammotion_device_records",
    CONF_MAMMOTION_JWT_INFO="mammotion_jwt_info",
    CONF_MAMMOTION_MQTT="mammotion_mqtt",
    CONF_PREFER_BLE="prefer_ble",
    CONF_MOW_PATH_FETCH_ENABLED="mow_path_fetch_enabled",
    CREDENTIAL_CACHE_KEYS=(
        "auth_data",
        "connect_data",
        "aep_data",
        "session_data",
        "region_data",
        "device_data",
        "mammotion_data",
        "mammotion_mqtt",
        "mammotion_jwt_info",
    ),
    DEVICE_SUPPORT=MagicMock(),
    COMMAND_EXCEPTIONS=(Exception,),
    EXPIRED_CREDENTIAL_EXCEPTIONS=(_ReLoginRequiredError, _LoginFailedError, Exception),
    NO_REQUEST_MODES=(),
    LOGGER=MagicMock(),
)

_coordinator_mod = _stub(
    "custom_components.mammotion.coordinator",
    MammotionBaseUpdateCoordinator=object,
    MammotionReportUpdateCoordinator=object,
    MammotionRTKCoordinator=object,
    MammotionSpinoCoordinator=object,
    MammotionDeviceErrorUpdateCoordinator=object,
    MammotionDeviceVersionUpdateCoordinator=object,
    MammotionMaintenanceUpdateCoordinator=object,
    MammotionMapUpdateCoordinator=object,
)
_stub("custom_components.mammotion.models", MammotionDevices=MagicMock(), MammotionMowerData=MagicMock(), MammotionRTKData=MagicMock(), MammotionSpinoData=MagicMock())
_stub("custom_components.mammotion.services", async_setup_services=MagicMock())
_stub("custom_components.mammotion.config", MammotionConfigStore=MagicMock(), async_get_store=MagicMock(), async_pop_store=MagicMock(), TRANSPORT_BLUETOOTH="bluetooth_enabled", TRANSPORT_CLOUD="cloud_enabled")

class _MammotionBaseEntity:
    """Minimal MammotionBaseEntity stub."""
    hass = None
    coordinator = None
    registry_entry = None
    def __init__(self, *args, **kwargs): pass
    def async_write_ha_state(self): pass
    async def async_remove(self): pass

class _MammotionBaseRTKEntity:
    """Minimal MammotionBaseRTKEntity stub."""
    hass = None
    def __init__(self, *args, **kwargs): pass

class _MammotionCameraBaseEntity:
    """Minimal MammotionCameraBaseEntity stub."""
    hass = None
    def __init__(self, *args, **kwargs): pass

class _MammotionBaseSpinoEntity:
    """Minimal MammotionBaseSpinoEntity stub."""
    hass = None
    def __init__(self, *args, **kwargs): pass

_entity_mod = _stub(
    "custom_components.mammotion.entity",
    MammotionBaseEntity=_MammotionBaseEntity,
    MammotionBaseRTKEntity=_MammotionBaseRTKEntity,
    MammotionCameraBaseEntity=_MammotionCameraBaseEntity,
    MammotionBaseSpinoEntity=_MammotionBaseSpinoEntity,
)

# ── Load switch.py directly (bypasses __init__.py entirely) ──────────────
import importlib.util
from pathlib import Path

_switch_path = Path(__file__).parent.parent / "custom_components" / "mammotion" / "switch.py"
_spec = importlib.util.spec_from_file_location("custom_components.mammotion.switch", _switch_path)
_switch_real = importlib.util.module_from_spec(_spec)
sys.modules["custom_components.mammotion.switch"] = _switch_real
_spec.loader.exec_module(_switch_real)
