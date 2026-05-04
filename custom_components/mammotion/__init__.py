"""The Mammotion integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aiohttp import ClientConnectorError
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HassJob, HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.event import async_call_later
from homeassistant.loader import async_get_integration
from pymammotion.aliyun.exceptions import TooManyRequestsException
from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.client import MammotionClient
from pymammotion.data.model.account import Credentials
from pymammotion.data.model.device import MowingDevice
from pymammotion.transport.base import LoginFailedError, TransportType
from Tea.exceptions import UnretryableException

from .const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_AUTH_DATA,
    CONF_BLE_DEVICES,
    CONF_CONNECT_DATA,
    CONF_DEVICE_DATA,
    CONF_DEVICE_NAME,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_MAMMOTION_DATA,
    CONF_MAMMOTION_DEVICE_LIST,
    CONF_MAMMOTION_DEVICE_RECORDS,
    CONF_MAMMOTION_JWT_INFO,
    CONF_MAMMOTION_MQTT,
    CONF_PREFER_BLE,
    CONF_REGION_DATA,
    CONF_SESSION_DATA,
    CONF_STAY_CONNECTED_BLUETOOTH,
    CONF_USE_WIFI,
    DEVICE_SUPPORT,
    DOMAIN,
    EXPIRED_CREDENTIAL_EXCEPTIONS,
    LOGGER,
)
from .coordinator import (
    MammotionDeviceErrorUpdateCoordinator,
    MammotionDeviceVersionUpdateCoordinator,
    MammotionMaintenanceUpdateCoordinator,
    MammotionMapUpdateCoordinator,
    MammotionReportUpdateCoordinator,
    MammotionRTKCoordinator,
)
from .models import MammotionDevices, MammotionMowerData, MammotionRTKData
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.LAWN_MOWER,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.CAMERA,
    Platform.UPDATE,
]

type MammotionConfigEntry = ConfigEntry[MammotionDevices]


def _has_ble_devices(entry: MammotionConfigEntry) -> bool:
    """Return True if the entry has at least one BLE device address."""
    return bool(entry.data.get(CONF_BLE_DEVICES))


async def _async_attempt_login(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    mammotion: MammotionClient,
    account: str,
    password: str,
    *,
    ble_fallback: bool,
) -> bool:
    """Attempt cloud login with credential-cache support.

    Returns True on success. Returns False when login fails and ``ble_fallback``
    is True (BLE devices are available as a fallback). Raises the appropriate
    ConfigEntry exception when login fails with no BLE fallback available.
    """
    session = aiohttp_client.async_get_clientsession(hass)
    cached = _load_cached_credentials(entry)
    try:
        if cached:
            await mammotion.restore_credentials(account, password, cached, session)
        else:
            await mammotion.login_and_initiate_cloud(account, password, session)
        return True
    except ClientConnectorError as err:
        raise ConfigEntryNotReady(err)
    except LoginFailedError as err:
        if ble_fallback:
            LOGGER.warning(
                "Mammotion login failed; continuing in BLE-only mode: %s", err
            )
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_HAS_CLOUD_ACCOUNT: False}
            )
            return False
        raise ConfigEntryAuthFailed(err) from err
    except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
        LOGGER.debug(exc)
        if cached:
            LOGGER.warning(
                "Aliyun cache is stale (%s) — clearing cached gateway credentials",
                exc,
            )
            stale_keys = (
                CONF_AEP_DATA,
                CONF_AUTH_DATA,
                CONF_REGION_DATA,
                CONF_SESSION_DATA,
                CONF_DEVICE_DATA,
                CONF_CONNECT_DATA,
                CONF_MAMMOTION_DATA,
            )
            hass.config_entries.async_update_entry(
                entry,
                data={k: v for k, v in entry.data.items() if k not in stale_keys},
            )
        try:
            await mammotion.login_and_initiate_cloud(
                account, password, aiohttp_client.async_get_clientsession(hass)
            )
            return True
        except LoginFailedError as retry_err:
            if ble_fallback:
                LOGGER.warning(
                    "Login failed after cache clear; continuing in BLE-only mode: %s",
                    retry_err,
                )
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_HAS_CLOUD_ACCOUNT: False}
                )
                return False
            raise ConfigEntryAuthFailed(retry_err) from retry_err
    except TooManyRequestsException as err:
        if ble_fallback:
            LOGGER.warning("Mammotion API rate limited; continuing in BLE-only mode")
            return False
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="api_limit_exceeded"
        ) from err
    except UnretryableException as err:
        if ble_fallback:
            LOGGER.warning(
                "Unretryable login error; continuing in BLE-only mode: %s", err
            )
            return False
        raise ConfigEntryError(err)


async def _attach_ble_to_mower(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    mammotion: MammotionClient,
    device: Device,
    ble_address: str,
) -> None:
    """Attach a BLE transport to a mower device and register a persistent update callback."""
    mowing_device = mammotion.get_device_by_name(device.device_name)
    if mowing_device is not None:
        mowing_device.mower_state.ble_mac = ble_address

    ble_device = bluetooth.async_ble_device_from_address(
        hass, ble_address.upper(), True
    )
    if ble_device:
        await mammotion.add_ble_to_device(device.device_name, ble_device)

    _device_name = device.device_name


async def _attach_ble_to_rtk(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    mammotion: MammotionClient,
    rtk: Device,
    ble_address: str,
) -> None:
    """Attach a BLE transport to an RTK base station and register a persistent update callback."""
    rtk_device = mammotion.get_device_by_name(rtk.device_name)
    if rtk_device is not None:
        rtk_device.ble_mac = ble_address

    ble_device = bluetooth.async_ble_device_from_address(
        hass, ble_address.upper(), True
    )
    if ble_device:
        await mammotion.add_ble_to_device(rtk.device_name, ble_device)


def _register_ble_reconnect_callback(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    mammotion: MammotionClient,
    device_name: str,
    ble_address: str,
) -> None:
    """Register a persistent BLE callback to reconnect when a device comes in range."""

    def _ble_seen(
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        handle = mammotion.mower(device_name)
        if handle is None or handle.has_transport(TransportType.BLE):
            return
        hass.async_create_task(
            mammotion.add_ble_to_device(device_name, service_info.device)
        )

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _ble_seen,
            BluetoothCallbackMatcher(address=ble_address.upper()),
            BluetoothScanningMode.ACTIVE,
        )
    )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Mammotion integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> bool:
    """Set up Mammotion from a config entry."""

    addresses = entry.data.get(CONF_BLE_DEVICES, {})
    integration = await async_get_integration(hass, DOMAIN)
    mammotion = MammotionClient(ha_version=integration.version)
    account = entry.data.get(CONF_ACCOUNTNAME)
    password = entry.data.get(CONF_PASSWORD)
    use_wifi = entry.data.get(CONF_USE_WIFI, True)

    # Migrate options: move from stay_connected_bluetooth to prefer_ble default.
    if not entry.options:
        hass.config_entries.async_update_entry(entry, options={CONF_PREFER_BLE: True})
    elif (
        CONF_STAY_CONNECTED_BLUETOOTH in entry.options
        and CONF_PREFER_BLE not in entry.options
    ):
        new_opts = {
            k: v for k, v in entry.options.items() if k != CONF_STAY_CONNECTED_BLUETOOTH
        }
        new_opts[CONF_PREFER_BLE] = True
        hass.config_entries.async_update_entry(entry, options=new_opts)

    prefer_ble = entry.options.get(CONF_PREFER_BLE, True)

    # Default to True for older entries that predate this key, as long as they
    # have account credentials configured.
    has_cloud_account = entry.data.get(
        CONF_HAS_CLOUD_ACCOUNT, bool(account and password)
    )

    mammotion_mowers: list[MammotionMowerData] = []
    mammotion_devices: MammotionDevices = MammotionDevices([], [])
    mammotion_rtk: list[MammotionRTKData] = []

    cloud_available = False

    if has_cloud_account and account and password:
        cloud_available = await _async_attempt_login(
            hass,
            entry,
            mammotion,
            account,
            password,
            ble_fallback=_has_ble_devices(entry),
        )

    if cloud_available:
        store_cloud_credentials(hass, entry, mammotion)

        mower_devices, mammotion_rtk_devices = _build_device_list(mammotion)

        for device in mower_devices:
            if device_ble_address := addresses.get(device.device_name, None):
                await _attach_ble_to_mower(
                    hass,
                    entry,
                    mammotion,
                    device,
                    device_ble_address,
                )

            if not use_wifi:
                mammotion.set_prefer_ble(device.device_name, prefer_ble=True)
                handle = mammotion.mower(device.device_name)
                if handle is not None:
                    for t_type in (
                        TransportType.CLOUD_ALIYUN,
                        TransportType.CLOUD_MAMMOTION,
                    ):
                        await handle.disconnect_transport(t_type)
            elif prefer_ble:
                mammotion.set_prefer_ble(device.device_name, prefer_ble=True)

            unique_name = device.device_name

            maintenance_coordinator = MammotionMaintenanceUpdateCoordinator(
                hass, entry, device, mammotion, unique_name=unique_name
            )
            version_coordinator = MammotionDeviceVersionUpdateCoordinator(
                hass, entry, device, mammotion, unique_name=unique_name
            )
            report_coordinator = MammotionReportUpdateCoordinator(
                hass, entry, device, mammotion, unique_name=unique_name
            )
            map_coordinator = MammotionMapUpdateCoordinator(
                hass, entry, device, mammotion, unique_name=unique_name
            )
            error_coordinator = MammotionDeviceErrorUpdateCoordinator(
                hass, entry, device, mammotion, unique_name=unique_name
            )
            await report_coordinator.async_restore_data()
            await version_coordinator.async_config_entry_first_refresh()

            await report_coordinator.async_config_entry_first_refresh()
            await maintenance_coordinator.async_config_entry_first_refresh()

            await error_coordinator.async_config_entry_first_refresh()
            await map_coordinator._async_setup()

            mammotion_mowers.append(
                MammotionMowerData(
                    name=device.device_name,
                    unique_name=unique_name,
                    device=device,
                    api=mammotion,
                    maintenance_coordinator=maintenance_coordinator,
                    reporting_coordinator=report_coordinator,
                    version_coordinator=version_coordinator,
                    map_coordinator=map_coordinator,
                    error_coordinator=error_coordinator,
                )
            )

            async def _async_refresh_map(_: datetime) -> None:
                """Call the debouncer at a later time."""
                await map_coordinator.async_request_refresh()

            async_call_later(
                hass,
                1,
                HassJob(
                    _async_refresh_map,
                    "map-coordinator-refresh",
                    cancel_on_shutdown=True,
                ),
            )

        for rtk in mammotion_rtk_devices:
            if rtk_ble_address := addresses.get(rtk.device_name, None):
                await _attach_ble_to_rtk(
                    hass,
                    entry,
                    mammotion,
                    rtk,
                    rtk_ble_address,
                )

            rtk_unique_name = rtk.device_name
            rtk_coordinator = MammotionRTKCoordinator(
                hass, entry, rtk, mammotion, unique_name=rtk_unique_name
            )
            await rtk_coordinator.async_restore_data()
            await rtk_coordinator.async_config_entry_first_refresh()
            mammotion_rtk.append(
                MammotionRTKData(
                    name=rtk.device_name,
                    unique_name=rtk_unique_name,
                    api=mammotion,
                    device=rtk,
                    coordinator=rtk_coordinator,
                )
            )

    elif addresses:
        # BLE-only mode: either the user set use_wifi=False, has no account, or
        # cloud login failed and we are falling back to BLE for each known device.
        for device_name, ble_address in addresses.items():
            ble_device = bluetooth.async_ble_device_from_address(
                hass, ble_address.upper(), True
            )
            if ble_device is None:
                raise ConfigEntryNotReady(
                    f"BLE device {device_name} ({ble_address}) not in range"
                )

            await mammotion.add_ble_only_device(
                device_id=device_name,
                device_name=device_name,
                ble_device=ble_device,
                initial_device=MowingDevice(name=device_name),
            )

            _register_ble_reconnect_callback(
                hass, entry, mammotion, device_name, ble_address
            )

            synthetic_device = _create_ble_only_device(device_name)
            unique_name = device_name

            maintenance_coordinator = MammotionMaintenanceUpdateCoordinator(
                hass, entry, synthetic_device, mammotion, unique_name=unique_name
            )
            version_coordinator = MammotionDeviceVersionUpdateCoordinator(
                hass, entry, synthetic_device, mammotion, unique_name=unique_name
            )
            report_coordinator = MammotionReportUpdateCoordinator(
                hass, entry, synthetic_device, mammotion, unique_name=unique_name
            )
            map_coordinator = MammotionMapUpdateCoordinator(
                hass, entry, synthetic_device, mammotion, unique_name=unique_name
            )
            error_coordinator = MammotionDeviceErrorUpdateCoordinator(
                hass, entry, synthetic_device, mammotion, unique_name=unique_name
            )

            await report_coordinator.async_restore_data()
            await version_coordinator.async_config_entry_first_refresh()
            await report_coordinator.async_config_entry_first_refresh()
            await maintenance_coordinator.async_config_entry_first_refresh()
            await error_coordinator.async_config_entry_first_refresh()
            await map_coordinator._async_setup()

            mammotion_mowers.append(
                MammotionMowerData(
                    name=device_name,
                    unique_name=unique_name,
                    device=synthetic_device,
                    api=mammotion,
                    maintenance_coordinator=maintenance_coordinator,
                    reporting_coordinator=report_coordinator,
                    version_coordinator=version_coordinator,
                    map_coordinator=map_coordinator,
                    error_coordinator=error_coordinator,
                )
            )

    mammotion_devices.RTK = mammotion_rtk
    mammotion_devices.mowers = mammotion_mowers
    entry.runtime_data = mammotion_devices

    mammotion.setup_all_mower_watchers()

    async def shutdown_mammotion(_: Event | None = None) -> None:
        await mammotion.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown_mammotion)
    )
    entry.async_on_unload(shutdown_mammotion)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _build_device_list(
    mammotion: MammotionClient,
) -> tuple[list[Device], list[Device]]:
    """Return (mower_devices, rtk_devices).

    Combines Aliyun cloud devices and Mammotion-direct devices, filters out
    unsupported device types, and separates RTK/Trackers.
    """
    all_devices: list[Device] = [
        *mammotion.aliyun_device_list,
        *mammotion.mammotion_device_list,
    ]
    rtk_devices: list[Device] = []
    mower_devices: list[Device] = []

    for device in all_devices:
        if not device.device_name.startswith(DEVICE_SUPPORT):
            if device.category_key == "Tracker":
                rtk_devices.append(device)
            continue
        mower_devices.append(device)

    return mower_devices, rtk_devices


def _create_ble_only_device(device_name: str) -> Device:
    """Create a synthetic Device record for a BLE-only mower (no cloud account)."""
    return Device(
        gmt_modified=0,
        node_type="DEVICE",
        device_name=device_name,
        product_name=device_name,
        status=1,
        identity_id=device_name,
        net_type="BLE",
        category_key="",
        product_key="",
        is_edge_gateway=False,
        category_name="",
        identity_alias=device_name,
        iot_id="",
        bind_time=0,
        owned=1,
        thing_type="DEVICE",
    )


# The library's to_cache()/from_cache() use "connect_response" as the key for the
# connect response, but HA's config entry historically stored it as "connect_data".
# This map translates between the two; all other keys are identical.
_LIBRARY_TO_HA_KEY: dict[str, str] = {"connect_response": CONF_CONNECT_DATA}
_HA_TO_LIBRARY_KEY: dict[str, str] = {v: k for k, v in _LIBRARY_TO_HA_KEY.items()}


def store_cloud_credentials(
    hass: HomeAssistant,
    config_entry: MammotionConfigEntry,
    client: MammotionClient,
) -> None:
    """Persist cloud credentials from the client into the config entry."""
    cache = client.to_cache()
    if not cache:
        return
    translated = {_LIBRARY_TO_HA_KEY.get(k, k): v for k, v in cache.items()}
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, **translated},
    )


def _load_cached_credentials(entry: MammotionConfigEntry) -> dict[str, Any]:
    """Translate HA config-entry keys to library cache keys.

    Returns the translated dict when at least one credential path's sentinel
    keys are present and non-None, otherwise returns an empty dict so the
    caller knows to fall back to a full login.
    """
    library_data = {_HA_TO_LIBRARY_KEY.get(k, k): v for k, v in entry.data.items()}
    has_aliyun = bool(library_data.get("aep_data"))
    has_mammotion = bool(library_data.get("mammotion_mqtt")) and bool(
        library_data.get("mammotion_device_records")
    )
    return library_data if (has_aliyun or has_mammotion) else {}


async def _async_update_listener(
    hass: HomeAssistant, entry: MammotionConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for mower in entry.runtime_data.mowers:
            mower.maintenance_coordinator.store_cloud_credentials()
            try:
                if handle := mower.api.mower(mower.name):
                    await handle.stop()
                mower.api.teardown_device_watchers(mower.name)
                mower.api.remove_device(mower.name)
            except TimeoutError:
                """Do nothing as this sometimes occurs with disconnecting BLE."""
    return bool(unload_ok)


async def async_remove_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> None:
    """Remove stored data when the integration is deleted."""
    for mower in entry.runtime_data.mowers:
        await mower.reporting_coordinator.remove_saved_data()

    if not hass.config_entries.async_entries(DOMAIN):
        hass.data.pop(DOMAIN, None)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: MammotionConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    device_identifier = next(
        (
            identifier[1]
            for identifier in device_entry.identifiers
            if identifier[0] == DOMAIN
        ),
        None,
    )
    mower = next(
        (
            mower
            for mower in config_entry.runtime_data.mowers
            if mower.unique_name == device_identifier
        ),
        None,
    )

    return not bool(mower)
