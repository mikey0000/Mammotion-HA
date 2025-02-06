"""The Mammotion Luba integration."""

from __future__ import annotations

from aiohttp import ClientConnectorError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_MAC, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from pymammotion import CloudIOTGateway
from pymammotion.aliyun.model.aep_response import AepResponse
from pymammotion.aliyun.model.connect_response import ConnectResponse
from pymammotion.aliyun.model.dev_by_account_response import ListingDevByAccountResponse
from pymammotion.aliyun.model.login_by_oauth_response import LoginByOAuthResponse
from pymammotion.aliyun.model.regions_response import RegionResponse
from pymammotion.aliyun.model.session_by_authcode_response import (
    SessionByAuthCodeResponse,
)
from pymammotion.data.model.account import Credentials
from pymammotion.http.http import MammotionHTTP
from pymammotion.http.model.http import LoginResponseData, Response
from pymammotion.mammotion.devices.mammotion import Mammotion
from pymammotion.utility.device_config import DeviceConfig

from .const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_AUTH_DATA,
    CONF_CONNECT_DATA,
    CONF_DEVICE_DATA,
    CONF_DEVICE_NAME,
    CONF_MAMMOTION_DATA,
    CONF_REGION_DATA,
    CONF_RETRY_COUNT,
    CONF_SESSION_DATA,
    CONF_STAY_CONNECTED_BLUETOOTH,
    DEFAULT_RETRY_COUNT,
    DOMAIN,
    EXPIRED_CREDENTIAL_EXCEPTIONS,
    LOGGER,
)
from .coordinator import (
    MammotionDataUpdateCoordinator,
    MammotionDeviceVersionUpdateCoordinator,
    MammotionMaintenanceUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)
from .models import MammotionDevices, MammotionMowerData

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.LAWN_MOWER,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
]

type MammotionConfigEntry = ConfigEntry[MammotionDevices]


async def async_setup_entry(hass: HomeAssistant, entry: MammotionConfigEntry) -> bool:
    """Set up Mammotion Luba from a config entry."""
    assert entry.unique_id is not None

    # if not entry.unique_id:
    #     hass.config_entries.async_update_entry(entry, unique_id="some_uuid")

    if CONF_ADDRESS not in entry.data and CONF_MAC in entry.data:
        # Bleak uses addresses not mac addresses which are actually
        # UUIDs on some platforms (MacOS).
        mac = entry.data[CONF_MAC]
        if "-" not in mac:
            mac = dr.format_mac(mac)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_ADDRESS: mac},
        )

    if not entry.options:
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_STAY_CONNECTED_BLUETOOTH: False},
        )

    device_name = entry.data.get(CONF_DEVICE_NAME)
    mammotion = Mammotion()
    account = entry.data.get(CONF_ACCOUNTNAME)
    password = entry.data.get(CONF_PASSWORD)

    mammotion_devices: list[MammotionMowerData] = []

    if account and password:
        credentials = Credentials()
        credentials.email = account
        credentials.password = password
        try:
            cloud_client = await check_and_restore_cloud(hass, entry)
            if cloud_client is None:
                await mammotion.login_and_initiate_cloud(account, password)
            else:
                await mammotion.initiate_cloud_connection(account, cloud_client)
        except ClientConnectorError as err:
            raise ConfigEntryNotReady(err)
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            LOGGER.debug(exc)
            await mammotion.login_and_initiate_cloud(account, password, True)

        if mqtt_client := mammotion.mqtt_list.get(account):
            for (
                device
            ) in mqtt_client.cloud_client.devices_by_account_response.data.data:
                maintenance_coordinator = MammotionMaintenanceUpdateCoordinator(
                    hass, entry, device, mammotion
                )
                version_coordinator = MammotionDeviceVersionUpdateCoordinator(
                    hass, entry, device, mammotion
                )
                report_coordinator = MammotionReportUpdateCoordinator(
                    hass, entry, device, mammotion
                )
                # other coordinator
                await maintenance_coordinator.async_config_entry_first_refresh()
                await version_coordinator.async_config_entry_first_refresh()
                await report_coordinator.async_config_entry_first_refresh()

                # maintenance_coordinator.
                device_info = DeviceInfo(
                    identifiers={(DOMAIN, device.deviceName)},
                    manufacturer="Mammotion",
                    serial_number=device.deviceName.split("-", 1)[-1],
                    model_id=device.productModel,
                    name=device.nickName,
                    model=device.productName,
                    suggested_area="Garden",
                )

                device_config = DeviceConfig()
                if (
                    device_limits := device_config.get_working_parameters(
                        device.productKey
                    )
                    is None
                ):
                    device_limits = device_config.get_working_parameters(
                        version_coordinator.data.main_product_type
                    )

                mammotion_devices.append(
                    MammotionMowerData(
                        name=device.deviceName,
                        device=device_info,
                        device_limits=device_limits,
                        api=mammotion,
                        maintenance_coordinator=maintenance_coordinator,
                        reporting_coordinator=report_coordinator,
                        version_coordinator=version_coordinator,
                    )
                )

    entry.runtime_data = mammotion_devices
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def check_and_restore_cloud(
    hass: HomeAssistant, entry: MammotionConfigEntry
) -> CloudIOTGateway | None:
    """Check and restore previous cloud connection."""

    auth_data = entry.data.get(CONF_AUTH_DATA)
    region_data = entry.data.get(CONF_REGION_DATA)
    aep_data = entry.data.get(CONF_AEP_DATA)
    session_data = entry.data.get(CONF_SESSION_DATA)
    device_data = entry.data.get(CONF_DEVICE_DATA)
    connect_data = entry.data.get(CONF_CONNECT_DATA)
    mammotion_data = entry.data.get(CONF_MAMMOTION_DATA)

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
        session_by_authcode_response=SessionByAuthCodeResponse.from_dict(session_data)
        if isinstance(session_data, dict)
        else session_data,
        dev_by_account=ListingDevByAccountResponse.from_dict(device_data)
        if isinstance(device_data, dict)
        else device_data,
        login_by_oauth_response=LoginByOAuthResponse.from_dict(auth_data)
        if isinstance(auth_data, dict)
        else auth_data,
    )

    if isinstance(mammotion_data, dict):
        mammotion_data = Response[LoginResponseData].from_dict(mammotion_data)

    cloud_client.set_http(MammotionHTTP(response=mammotion_data))

    await hass.async_add_executor_job(cloud_client.check_or_refresh_session)

    return cloud_client


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.manager.remove_device(entry.runtime_data.device_name)
    return unload_ok
