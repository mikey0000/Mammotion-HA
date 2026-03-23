"""The Mammotion integration."""

from __future__ import annotations

from datetime import datetime

from aiohttp import ClientConnectorError
from homeassistant.components import bluetooth
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HassJob, HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.event import async_call_later
from pymammotion import CloudIOTGateway
from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.data.model.account import Credentials
from pymammotion.http.http import MammotionHTTP
from pymammotion.client import MammotionClient
from pymammotion.mammotion.devices.mammotion import ConnectionPreference
from pymammotion.mammotion.devices.mammotion_cloud import MammotionCloud
from pymammotion.utility.device_config import DeviceConfig
from Tea.exceptions import UnretryableException

from .const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_AUTH_DATA,
    CONF_BLE_DEVICES,
    CONF_CONNECT_DATA,
    CONF_DEVICE_DATA,
    CONF_DEVICE_NAME,
    CONF_MAMMOTION_DATA,
    CONF_MAMMOTION_DEVICE_LIST,
    CONF_MAMMOTION_DEVICE_RECORDS,
    CONF_MAMMOTION_JWT_INFO,
    CONF_MAMMOTION_MQTT,
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


async def async_setup_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> bool:
    """Set up Mammotion from a config entry."""

    addresses = entry.data.get(CONF_BLE_DEVICES, {})
    mammotion = MammotionClient()
    account = entry.data.get(CONF_ACCOUNTNAME)
    password = entry.data.get(CONF_PASSWORD)

    stay_connected_ble = entry.data.get(CONF_STAY_CONNECTED_BLUETOOTH, False)

    if not entry.options:
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_STAY_CONNECTED_BLUETOOTH: stay_connected_ble},
        )

    stay_connected_ble = entry.options.get(CONF_STAY_CONNECTED_BLUETOOTH, False)

    use_wifi = entry.data.get(CONF_USE_WIFI, True)

    mammotion_mowers: list[MammotionMowerData] = []
    mammotion_devices: MammotionDevices = MammotionDevices([], [])
    mammotion_rtk: list[MammotionRTKData] = []
    mammotion_rtk_devices: list[Device] = []

    if account and password:
        credentials = Credentials()
        credentials.email = account
        credentials.password = password
        try:
            cloud_client = await check_and_restore_cloud(entry)
            if cloud_client is None:
                await mammotion.login_and_initiate_cloud(account, password)
            else:
                # sometimes mammotion_data is missing....
                if cloud_client.mammotion_http is None:
                    mammotion_http = MammotionHTTP(account, password)
                    await mammotion_http.login_v2(account, password)
                    cloud_client.set_http(mammotion_http)

                await cloud_client.mammotion_http.get_user_device_list()
                await cloud_client.mammotion_http.get_user_device_page()
                await mammotion.initiate_cloud_connection(account, cloud_client)
        except ClientConnectorError as err:
            raise ConfigEntryNotReady(err)
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            LOGGER.debug(exc)
            await mammotion.login_and_initiate_cloud(account, password, True)
        except UnretryableException as err:
            raise ConfigEntryError(err)

        aliyun_mqtt_client = mammotion.mqtt_list.get(f"{account}_aliyun")
        mammotion_mqtt_client = mammotion.mqtt_list.get(f"{account}_mammotion")

        if aliyun_mqtt_client or mammotion_mqtt_client:
            if aliyun_mqtt_client:
                mqtt_client = aliyun_mqtt_client
            else:
                mqtt_client = mammotion_mqtt_client
            store_cloud_credentials(hass, entry, mqtt_client.cloud_client)

        mower_devices, shimed_cloud_devices, mammotion_rtk_devices = _build_device_list(
            mammotion, aliyun_mqtt_client, mammotion_mqtt_client
        )
        # Reconstruct cloud_devices for the per-device MQTT client lookup below
        cloud_devices: list[Device] = (
            aliyun_mqtt_client.cloud_client.devices_by_account_response.data.data
            if aliyun_mqtt_client
            else []
        )

        for device in mower_devices:
            if device in shimed_cloud_devices:
                mammotion_device = mammotion.get_or_create_device_by_name(
                    device, mammotion_mqtt_client, None
                )
            elif device in cloud_devices:
                mammotion_device = mammotion.get_or_create_device_by_name(
                    device, aliyun_mqtt_client, None
                )
            else:
                mammotion_device = mammotion.get_or_create_device_by_name(device, None)

            if device_ble_address := addresses.get(device.device_name, None):
                mammotion_device.state.mower_state.ble_mac = device_ble_address
                ble_device = bluetooth.async_ble_device_from_address(
                    hass, device_ble_address.upper(), True
                )
                if ble_device:
                    ble = mammotion_device.add_ble(ble_device)
                    ble.set_disconnect_strategy(disconnect=not stay_connected_ble)

            maintenance_coordinator = MammotionMaintenanceUpdateCoordinator(
                hass, entry, device, mammotion
            )
            version_coordinator = MammotionDeviceVersionUpdateCoordinator(
                hass, entry, device, mammotion
            )
            report_coordinator = MammotionReportUpdateCoordinator(
                hass, entry, device, mammotion
            )
            map_coordinator = MammotionMapUpdateCoordinator(
                hass, entry, device, mammotion
            )
            error_coordinator = MammotionDeviceErrorUpdateCoordinator(
                hass, entry, device, mammotion
            )
            # sometimes device is not there when restoring data
            await report_coordinator.async_restore_data()
            await version_coordinator.async_config_entry_first_refresh()

            await report_coordinator.async_config_entry_first_refresh()
            await maintenance_coordinator.async_config_entry_first_refresh()

            await error_coordinator.async_config_entry_first_refresh()
            await map_coordinator._async_setup()

            device_config = DeviceConfig()
            device_limits = device_config.get_working_parameters(
                version_coordinator.data.mower_state.sub_model_id
            )
            if device_limits is None:
                device_limits = device_config.get_working_parameters(device.product_key)

            if device_limits is None:
                device_limits = device_config.get_best_default(device.product_key)

            if not use_wifi:
                mammotion_device.preference = ConnectionPreference.BLUETOOTH
                await mammotion_device.cloud.stop()
                mammotion_device.cloud.mqtt.disconnect() if mammotion_device.cloud.mqtt.is_connected() else None
                # not entirely sure this is a good idea
                mammotion_device.remove_cloud()

            mammotion_mowers.append(
                MammotionMowerData(
                    name=device.device_name,
                    device=device,
                    device_limits=device_limits,
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
            if rtk in shimed_cloud_devices:
                mqtt_client = mammotion_mqtt_client
            else:
                mqtt_client = aliyun_mqtt_client
            rtk_coordinator = MammotionRTKCoordinator(
                hass, entry, rtk, mqtt_client, mammotion
            )
            await rtk_coordinator.async_config_entry_first_refresh()
            mammotion_rtk.append(
                MammotionRTKData(
                    name=rtk.device_name,
                    api=mammotion,
                    device=rtk,
                    coordinator=rtk_coordinator,
                )
            )

    # if not any(mammotion.get_device_by_name(mammotion_device.device.device_name).preference == ConnectionPreference.WIFI for mammotion_device in mammotion_devices):
    #     for mammotion_device in mammotion_devices:
    #         mower = mammotion.get_device_by_name(mammotion_device.device.device_name)
    #         await mower.cloud.stop()
    #         mower.cloud.mqtt.disconnect() if mower.cloud.mqtt.is_connected() else None
    #         mower.remove_cloud()
    mammotion_devices.RTK = mammotion_rtk
    mammotion_devices.mowers = mammotion_mowers
    entry.runtime_data = mammotion_devices

    async def shutdown_mammotion(_: Event | None = None):
        await mammotion.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown_mammotion)
    )
    entry.async_on_unload(shutdown_mammotion)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Record the path to the static files needed for WebRTC
    if hasattr(hass, "http"):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    "/mammotion_webrtc",
                    hass.config.path("custom_components/mammotion/www"),
                    cache_headers=False,
                )
            ]
        )

    # Make sure the 'www' folder exists
    import os

    www_dir = hass.config.path("custom_components/mammotion/www")
    os.makedirs(www_dir, exist_ok=True)

    return True


def _build_device_list(
    mammotion: MammotionClient,
    aliyun_mqtt_client: MammotionCloud | None,
    mammotion_mqtt_client: MammotionCloud | None,
) -> tuple[list[Device], list[Device], list[Device]]:
    """Return (mower_devices, shimmed_mammotion_devices, rtk_devices).

    Separates Aliyun cloud devices from Mammotion-direct devices,
    filters out unsupported device types, and separates RTK/Trackers.
    """
    shimed_cloud_devices: list[Device] = []
    cloud_devices: list[Device] = []
    rtk_devices: list[Device] = []
    mower_devices: list[Device] = []

    if mammotion_mqtt_client:
        shimed_cloud_devices = mammotion.shim_cloud_devices(
            mammotion_mqtt_client.cloud_client.mammotion_http.device_records.records
        )
    if aliyun_mqtt_client:
        cloud_devices = (
            aliyun_mqtt_client.cloud_client.devices_by_account_response.data.data
        )

    for device in [*shimed_cloud_devices, *cloud_devices]:
        if not device.device_name.startswith(DEVICE_SUPPORT):
            if device.category_key == "Tracker":
                rtk_devices.append(device)
            continue
        mower_devices.append(device)

    return mower_devices, shimed_cloud_devices, rtk_devices


# The library's to_cache()/from_cache() use "connect_response" as the key for the
# connect response, but HA's config entry historically stored it as "connect_data".
# This map translates between the two; all other keys are identical.
_LIBRARY_TO_HA_KEY: dict[str, str] = {"connect_response": CONF_CONNECT_DATA}
_HA_TO_LIBRARY_KEY: dict[str, str] = {v: k for k, v in _LIBRARY_TO_HA_KEY.items()}


def store_cloud_credentials(
    hass: HomeAssistant,
    config_entry: MammotionConfigEntry,
    cloud_client: CloudIOTGateway,
) -> None:
    """Persist cloud credentials from cloud_client into the config entry."""
    if cloud_client is None:
        return
    cache = cloud_client.to_cache()
    # Translate library cache keys → HA config-entry keys.
    translated = {_LIBRARY_TO_HA_KEY.get(k, k): v for k, v in cache.items()}
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, **translated},
    )


def _is_cloud_cache_complete(entry: MammotionConfigEntry) -> bool:
    """Return True if all required cloud credential keys are present and non-None."""
    required_keys = (
        CONF_REGION_DATA,
        CONF_AUTH_DATA,
        CONF_AEP_DATA,
        CONF_SESSION_DATA,
        CONF_DEVICE_DATA,
        CONF_CONNECT_DATA,
        CONF_MAMMOTION_DATA,
    )
    if any(k not in entry.data for k in required_keys):
        return False
    return not any(entry.data[k] is None for k in required_keys)


async def _reconstruct_cloud_client(entry: MammotionConfigEntry) -> CloudIOTGateway | None:
    """Reconstruct a CloudIOTGateway from cached config entry data via the library.

    Translates HA config-entry keys back to the library's from_cache() key names,
    then delegates all deserialization to CloudIOTGateway.from_cache().
    Returns None if reconstruction or session refresh fails.
    """
    account = entry.data.get(CONF_ACCOUNTNAME, "")
    password = entry.data.get(CONF_PASSWORD, "")
    # Translate HA config-entry keys → library cache keys.
    library_data = {_HA_TO_LIBRARY_KEY.get(k, k): v for k, v in entry.data.items()}
    return await CloudIOTGateway.from_cache(library_data, account, password)


async def check_and_restore_cloud(
    entry: MammotionConfigEntry,
) -> CloudIOTGateway | None:
    """Check and restore previous cloud connection.

    Validates the cache is complete, then delegates reconstruction and session
    refresh to CloudIOTGateway.from_cache() via _reconstruct_cloud_client().
    """
    if not _is_cloud_cache_complete(entry):
        return None

    return await _reconstruct_cloud_client(entry)


async def _async_update_listener(
    hass: HomeAssistant, entry: MammotionConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for mower in entry.runtime_data.mowers:
            try:
                await mower.api.remove_device(mower.name)
            except TimeoutError:
                """Do nothing as this sometimes occurs with disconnecting BLE."""
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    mower_name = (
        next(
            identifier[1]
            for identifier in device_entry.identifiers
            if identifier[0] == DOMAIN
        ),
    )
    mower = next(
        (
            mower
            for mower in config_entry.runtime_data.mowers
            if mower.name == mower_name
        ),
        None,
    )

    return not bool(mower)
