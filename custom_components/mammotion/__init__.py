"""The Mammotion integration."""

from __future__ import annotations

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
from pymammotion.aliyun.model.aep_response import AepResponse
from pymammotion.aliyun.model.connect_response import ConnectResponse
from pymammotion.aliyun.model.dev_by_account_response import (
    Device,
    ListingDevAccountResponse,
)
from pymammotion.aliyun.model.login_by_oauth_response import LoginByOAuthResponse
from pymammotion.aliyun.model.regions_response import RegionResponse
from pymammotion.aliyun.model.session_by_authcode_response import (
    SessionByAuthCodeResponse,
)
from pymammotion.data.model.account import Credentials
from pymammotion.http.http import MammotionHTTP
from pymammotion.http.model.http import LoginResponseData, Response
from pymammotion.http.model.response_factory import response_factory
from pymammotion.mammotion.devices.mammotion import ConnectionPreference, Mammotion
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
    mammotion = Mammotion()
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

        device_list: list[Device] = []
        shimed_cloud_devices = []
        cloud_devices = []

        if mammotion_mqtt_client:
            shimed_cloud_devices = mammotion.shim_cloud_devices(
                mammotion_mqtt_client.cloud_client.mammotion_http.device_records.records
            )
            device_list.extend(shimed_cloud_devices)
        if aliyun_mqtt_client:
            cloud_devices = (
                aliyun_mqtt_client.cloud_client.devices_by_account_response.data.data
            )
            device_list.extend(cloud_devices)

        for device in device_list:
            if not device.device_name.startswith(DEVICE_SUPPORT):
                if device.category_key == "Tracker":
                    mammotion_rtk_devices.append(device)
                continue

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
            async_call_later(
                hass,
                1,
                HassJob(
                    lambda _: report_coordinator.async_config_entry_first_refresh(),
                    "report-coordinator-refresh",
                    cancel_on_shutdown=True,
                ),
            )
            async_call_later(
                hass,
                1,
                HassJob(
                    lambda _: maintenance_coordinator.async_config_entry_first_refresh(),
                    "maintenance-coordinator-refresh",
                    cancel_on_shutdown=True,
                ),
            )

            async_call_later(
                hass,
                1,
                HassJob(
                    lambda _: error_coordinator.async_config_entry_first_refresh(),
                    "error-coordinator-refresh",
                    cancel_on_shutdown=True,
                ),
            )

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
            try:
                async_call_later(
                    hass,
                    1,
                    HassJob(
                        lambda _: map_coordinator.async_request_refresh(),
                        "map-coordinator-refresh",
                        cancel_on_shutdown=True,
                    ),
                )
            except:
                """Do nothing for now."""

        for rtk in mammotion_rtk_devices:
            if rtk in shimed_cloud_devices:
                mqtt_client = mammotion_mqtt_client
            else:
                mqtt_client = aliyun_mqtt_client
            rtk_coordinator = MammotionRTKCoordinator(hass, entry, rtk, mqtt_client)
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


def store_cloud_credentials(
    hass: HomeAssistant,
    config_entry: MammotionConfigEntry,
    cloud_client: CloudIOTGateway,
) -> None:
    """Store cloud credentials in config entry."""

    if cloud_client is not None:
        mammotion_data = config_entry.data.get(CONF_MAMMOTION_DATA)
        if cloud_client.mammotion_http is not None:
            mammotion_data = cloud_client.mammotion_http.response

        config_updates = {
            **config_entry.data,
            CONF_CONNECT_DATA: cloud_client.connect_response,
            CONF_AUTH_DATA: cloud_client.login_by_oauth_response,
            CONF_REGION_DATA: cloud_client.region_response,
            CONF_AEP_DATA: cloud_client.aep_response,
            CONF_SESSION_DATA: cloud_client.session_by_authcode_response,
            CONF_DEVICE_DATA: cloud_client.devices_by_account_response,
            CONF_MAMMOTION_DATA: mammotion_data,
            CONF_MAMMOTION_MQTT: cloud_client.mammotion_http.mqtt_credentials,
            CONF_MAMMOTION_DEVICE_LIST: cloud_client.mammotion_http.device_info,
            CONF_MAMMOTION_DEVICE_RECORDS: cloud_client.mammotion_http.device_records,
            CONF_MAMMOTION_JWT_INFO: cloud_client.mammotion_http.jwt_info,
        }
        hass.config_entries.async_update_entry(config_entry, data=config_updates)


async def check_and_restore_cloud(
    entry: MammotionConfigEntry,
) -> CloudIOTGateway | None:
    """Check and restore previous cloud connection."""

    if any(
        k not in entry.data
        for k in (
            CONF_REGION_DATA,
            CONF_AUTH_DATA,
            CONF_AEP_DATA,
            CONF_SESSION_DATA,
            CONF_DEVICE_DATA,
            CONF_CONNECT_DATA,
            CONF_MAMMOTION_DATA,
        )
    ):
        return None

    auth_data = entry.data[CONF_AUTH_DATA]
    region_data = entry.data[CONF_REGION_DATA]
    aep_data = entry.data[CONF_AEP_DATA]
    session_data = entry.data[CONF_SESSION_DATA]
    device_data = entry.data[CONF_DEVICE_DATA]
    connect_data = entry.data[CONF_CONNECT_DATA]
    mammotion_data = entry.data[CONF_MAMMOTION_DATA]
    mammotion_mqtt = entry.data.get(CONF_MAMMOTION_MQTT, None)
    mammotion_device_list = entry.data.get(CONF_MAMMOTION_DEVICE_LIST, None)
    mammotion_device_records = entry.data.get(CONF_MAMMOTION_DEVICE_RECORDS, None)
    mammotion_jwt = entry.data.get(CONF_MAMMOTION_JWT_INFO, None)

    if any(
        data is None
        for data in [
            auth_data,
            region_data,
            aep_data,
            session_data,
            device_data,
            connect_data,
            mammotion_data,
        ]
    ):
        return None

    mammotion_response_data = (
        response_factory(Response[LoginResponseData], mammotion_data)
        if isinstance(mammotion_data, dict)
        else mammotion_data
    )
    account = entry.data.get(CONF_ACCOUNTNAME)
    password = entry.data.get(CONF_PASSWORD)

    mammotion_http = MammotionHTTP(account, password)
    mammotion_http.response = mammotion_response_data
    if mammotion_device_list:
        mammotion_http.device_info = mammotion_device_list
    if mammotion_device_records:
        mammotion_http.device_records = mammotion_device_records
    if mammotion_mqtt:
        mammotion_http.mqtt_credentials = mammotion_mqtt
    if mammotion_jwt:
        mammotion_http.jwt_info = mammotion_jwt
    mammotion_http.login_info = (
        LoginResponseData.from_dict(mammotion_response_data.data)
        if isinstance(mammotion_response_data.data, dict)
        else mammotion_response_data.data
    )

    try:
        cloud_client = CloudIOTGateway(
            connect_response=ConnectResponse.from_dict(connect_data)
            if isinstance(connect_data, dict)
            else connect_data,
            aep_response=AepResponse.from_dict(aep_data)
            if isinstance(aep_data, dict)
            else aep_data,
            region_response=RegionResponse.from_dict(region_data)
            if isinstance(region_data, dict)
            else region_data,
            session_by_authcode_response=SessionByAuthCodeResponse.from_dict(
                session_data
            )
            if isinstance(session_data, dict)
            else session_data,
            dev_by_account=ListingDevAccountResponse.from_dict(device_data)
            if isinstance(device_data, dict)
            else device_data,
            login_by_oauth_response=LoginByOAuthResponse.from_dict(auth_data)
            if isinstance(auth_data, dict)
            else auth_data,
            mammotion_http=mammotion_http,
        )
    except Exception:
        LOGGER.exception("Error while restoring cloud data")
        return None

    await cloud_client.check_or_refresh_session()
    return cloud_client


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
