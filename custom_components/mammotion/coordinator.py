"""Provides the mammotion DataUpdateCoordinator."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import betterproto
from aiohttp import ClientConnectorError
from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from mashumaro.exceptions import InvalidFieldValue
from pymammotion.aliyun.cloud_gateway import DeviceOfflineException, SetupException
from pymammotion.data.model import GenerateRouteInformation, HashList
from pymammotion.data.model.account import Credentials
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings, create_path_order
from pymammotion.mammotion.devices.mammotion import (
    ConnectionPreference,
    Mammotion,
)
from pymammotion.proto import has_field
from pymammotion.proto.luba_msg import LubaMsg
from pymammotion.proto.mctrl_sys import RptAct, RptInfoType

from .const import (
    COMMAND_EXCEPTIONS,
    CONF_ACCOUNTNAME,
    CONF_DEVICE_NAME,
    CONF_STAY_CONNECTED_BLUETOOTH,
    CONF_USE_WIFI,
    DOMAIN,
    LOGGER,
)

if TYPE_CHECKING:
    from . import MammotionConfigEntry

UPDATE_INTERVAL = timedelta(minutes=1)


class MammotionDataUpdateCoordinator(DataUpdateCoordinator[MowingDevice]):
    """Class to manage fetching mammotion data."""

    address: str | None = None
    config_entry: MammotionConfigEntry
    manager: Mammotion = None
    _operation_settings: OperationSettings

    def __init__(self, hass: HomeAssistant, config_entry: MammotionConfigEntry) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.device_name = None
        assert self.config_entry.unique_id
        self.config_entry = config_entry
        self._operation_settings = OperationSettings()
        self.update_failures = 0

    async def async_setup(self) -> None:
        """Set coordinator up."""
        ble_device = None
        credentials = None
        preference = (
            ConnectionPreference.WIFI
            if self.config_entry.data.get(CONF_USE_WIFI, False)
            else ConnectionPreference.BLUETOOTH
        )
        address = self.config_entry.data.get(CONF_ADDRESS)
        name = self.config_entry.data.get(CONF_DEVICE_NAME)
        account = self.config_entry.data.get(CONF_ACCOUNTNAME)
        password = self.config_entry.data.get(CONF_PASSWORD)
        stay_connected_ble = self.config_entry.options.get(
            CONF_STAY_CONNECTED_BLUETOOTH, False
        )

        if name:
            self.device_name = name

        if self.manager is None or self.manager.get_device_by_name(name) is None:
            self.manager = Mammotion()
            if account and password:
                credentials = Credentials()
                credentials.email = account
                credentials.password = password
                try:
                    await self.manager.login_and_initiate_cloud(account, password)
                except ClientConnectorError as err:
                    raise ConfigEntryNotReady(err)

                # address previous bugs
                if address is None and preference == ConnectionPreference.BLUETOOTH:
                    preference = ConnectionPreference.WIFI

            if address:
                ble_device = bluetooth.async_ble_device_from_address(self.hass, address)
                if not ble_device and credentials is None:
                    raise ConfigEntryNotReady(
                        f"Could not find Mammotion lawn mower with address {address}"
                    )
                if ble_device is not None:
                    self.device_name = ble_device.name or "Unknown"
                    self.address = address
                    self.manager.add_ble_device(ble_device, preference)

        if self.device_name is not None:
            device = self.manager.get_device_by_name(self.device_name)
        else:
            device_names = self.manager.devices.devices.keys()
            if len(device_names) == 0:
                raise ConfigEntryNotReady("no_devices")
            self.device_name = device_names[0]
            device = self.manager.get_device_by_name(device_names[0])
        device.preference = preference

        if ble_device and device:
            device.ble().set_disconnect_strategy(not stay_connected_ble)

        try:
            if preference is ConnectionPreference.WIFI and device.has_cloud():
                await device.cloud().start_sync(0)
                device.cloud().set_notification_callback(
                    self._async_update_notification
                )
            elif device.has_ble():
                await device.ble().start_sync(0)
                device.ble().set_notification_callback(self._async_update_notification)
            else:
                raise ConfigEntryNotReady(
                    "No configuration available to setup Mammotion lawn mower"
                )

        except COMMAND_EXCEPTIONS as exc:
            raise ConfigEntryNotReady("Unable to setup Mammotion device") from exc

        await self.async_restore_data()

    async def async_restore_data(self) -> None:
        """Restore saved data."""
        store = Store(self.hass, version=1, key=self.device_name)
        restored_data = await store.async_load()
        try:
            if restored_data:
                if device_dict := restored_data.get("device"):
                    restored_data["device"] = None
                else:
                    device_dict = LubaMsg().to_dict(casing=betterproto.Casing.SNAKE)

                self.data = MowingDevice().from_dict(restored_data)
                self.data.update_raw(device_dict)
                self.manager.get_device_by_name(
                    self.device_name
                ).mower_state = self.data
        except InvalidFieldValue:
            """invalid"""
            self.data = MowingDevice()
            self.manager.get_device_by_name(self.device_name).mower_state = self.data

    async def async_save_data(self, data: MowingDevice) -> None:
        """Get map data from the device."""
        store = Store(self.hass, version=1, key=self.device_name)
        stored_data = asdict(data)
        await store.async_save(stored_data)

    async def async_sync_maps(self) -> None:
        """Get map data from the device."""
        await self.manager.start_map_sync(self.device_name)

    async def async_start_stop_blades(self, start_stop: bool) -> None:
        if start_stop:
            await self.async_send_command("set_blade_control", on_off=1)
        else:
            await self.async_send_command("set_blade_control", on_off=0)

    async def async_set_sidelight(self, on_off: int) -> None:
        """Set Sidelight."""
        await self.async_send_command(
            "read_and_set_sidelight", is_sidelight=bool(on_off), operate=0
        )

    async def async_read_sidelight(self) -> None:
        """Set Sidelight."""
        await self.async_send_command(
            "read_and_set_sidelight", is_sidelight=False, operate=1
        )

    async def async_blade_height(self, height: int) -> int:
        """Set blade height."""
        await self.async_send_command("set_blade_height", height=float(height))
        return height

    async def async_leave_dock(self) -> None:
        """Leave dock."""
        await self.async_send_command("leave_dock")

    async def async_cancel_task(self) -> None:
        """Cancel task."""
        await self.async_send_command("cancel_job")

    async def async_move_forward(self, speed: float) -> None:
        """Move forward."""
        await self.async_send_command("move_forward", linear=speed)

    async def async_move_left(self, speed: float) -> None:
        """Move left."""
        await self.async_send_command("move_left", angular=speed)

    async def async_move_right(self, speed: float) -> None:
        """Move right."""
        await self.async_send_command("move_right", angular=speed)

    async def async_move_back(self, speed: float) -> None:
        """Move back."""
        await self.async_send_command("move_back", linear=speed)

    async def async_rtk_dock_location(self) -> None:
        """RTK and dock location."""
        await self.async_send_command("allpowerfull_rw", id=5, rw=1, context=1)

    async def async_request_iot_sync(self, stop: bool = False) -> None:
        """Sync specific info from device."""
        await self.async_send_command(
            "request_iot_sys",
            rpt_act=RptAct.RPT_STOP if stop else RptAct.RPT_START,
            rpt_info_type=[
                RptInfoType.RIT_DEV_STA,
                RptInfoType.RIT_DEV_LOCAL,
                RptInfoType.RIT_WORK,
            ],
            timeout=10000,
            period=3000,
            no_change_period=4000,
            count=0,
        )

    async def async_send_command(self, command: str, **kwargs: Any) -> None:
        """Send command."""
        try:
            await self.manager.send_command_with_args(
                self.device_name, command, **kwargs
            )
        except SetupException:
            await self.async_login()
        except DeviceOfflineException:
            """Device is offline try bluetooth if we have it."""
            try:
                if self.manager.get_device_by_name(self.device_name).ble():
                    await (
                        self.manager.get_device_by_name(self.device_name)
                        .ble()
                        .queue_command(command, **kwargs)
                    )
            except COMMAND_EXCEPTIONS as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="command_failed"
                ) from exc

    async def async_plan_route(self, operation_settings: OperationSettings) -> None:
        """Plan mow."""
        route_information = GenerateRouteInformation(
            one_hashs=operation_settings.areas,
            rain_tactics=operation_settings.rain_tactics,
            speed=operation_settings.speed,
            ultra_wave=operation_settings.ultra_wave,  # touch no touch etc
            toward=operation_settings.toward,  # is just angle
            toward_included_angle=operation_settings.toward_included_angle,  # angle relative to grid??
            toward_mode=operation_settings.toward_mode,
            blade_height=operation_settings.blade_height,
            channel_mode=operation_settings.channel_mode,  # line mode is grid single double or single2
            channel_width=operation_settings.channel_width,
            job_mode=operation_settings.job_mode,  # taskMode grid or border first
            edge_mode=operation_settings.border_mode,  # border laps
            path_order=create_path_order(operation_settings, self.device_name),
            obstacle_laps=operation_settings.obstacle_laps,
        )

        await self.async_send_command(
            "generate_route_information", generate_route_information=route_information
        )

    async def clear_all_maps(self) -> None:
        data = self.manager.get_device_by_name(self.device_name).mower_state
        data.map = HashList()

    async def _async_update_notification(self) -> None:
        """Update data from incoming messages."""
        mower = self.manager.mower(self.device_name)
        self.async_set_updated_data(mower)

    async def check_firmware_version(self) -> None:
        """Check if firmware version is udpated."""
        mower = self.manager.mower(self.device_name)
        device_registry = dr.async_get(self.hass)
        device_entry = device_registry.async_get_device(
            identifiers={(DOMAIN, self.device_name)}
        )
        if device_entry is None:
            return

        new_swversion = None
        if len(mower.net.toapp_devinfo_resp.resp_ids) > 0:
            new_swversion = mower.net.toapp_devinfo_resp.resp_ids[0].info

        if new_swversion is not None or new_swversion != device_entry.sw_version:
            device_registry.async_update_device(
                device_entry.id, sw_version=new_swversion
            )

        model_id = None
        if has_field(mower.sys.device_product_type_info):
            model_id = mower.sys.device_product_type_info.main_product_type

        if model_id is not None or model_id != device_entry.model_id:
            device_registry.async_update_device(device_entry.id, model_id=model_id)

    async def async_login(self) -> None:
        """Login to cloud servers."""
        await self.hass.async_add_executor_job(
            self.manager.get_device_by_name(self.device_name).cloud().mqtt.disconnect
        )
        account = self.config_entry.data.get(CONF_ACCOUNTNAME)
        password = self.config_entry.data.get(CONF_PASSWORD)
        await self.manager.login_and_initiate_cloud(account, password, True)

    async def _async_update_data(self) -> MowingDevice:
        """Get data from the device."""
        if self.update_failures > 10:
            """Don't hammer the mammotion/ali servers"""
            return self.data

        device = self.manager.get_device_by_name(self.device_name)
        await self.check_firmware_version()

        if self.address:
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self.address
            )

            if not ble_device and device.cloud() is None:
                self.update_failures += 1
                raise UpdateFailed("Could not find device")

            if ble_device and ble_device.name == device.name:
                if device.ble() is not None:
                    device.ble().update_device(ble_device)
                else:
                    device.add_ble(ble_device)

        try:
            if (
                len(device.mower_state.net.toapp_devinfo_resp.resp_ids) == 0
                or device.mower_state.net.toapp_wifi_iot_status.productkey is None
            ):
                await self.manager.start_sync(self.device_name, 0)

            await self.async_send_command("get_report_cfg")

        except COMMAND_EXCEPTIONS as exc:
            self.update_failures += 1
            raise UpdateFailed(f"Updating Mammotion device failed: {exc}") from exc
        except SetupException:
            self.update_failures += 1
            await self.async_login()
        except DeviceOfflineException:
            """Device is offline try bluetooth if we have it."""
            if device.ble():
                await device.ble().command("get_report_cfg")
            # TODO set a sensor to offline

        LOGGER.debug("Updated Mammotion device %s", self.device_name)
        LOGGER.debug("================= Debug Log =================")
        LOGGER.debug(
            "Mammotion device data: %s",
            asdict(self.manager.get_device_by_name(self.device_name).mower_state),
        )
        LOGGER.debug("==================================")

        self.update_failures = 0
        data = self.manager.get_device_by_name(self.device_name).mower_state
        await self.async_save_data(data)
        return data

    @property
    def operation_settings(self) -> OperationSettings:
        """Return operation settings for planning."""
        return self._operation_settings

    # TODO when submitting to HA use this 2024.8 and up
    # async def _async_setup(self) -> None:
    #     try:
    #         await self.async_setup()
    #     except COMMAND_EXCEPTIONS as exc:
    #         raise UpdateFailed(f"Setting up Mammotion device failed: {exc}") from exc
