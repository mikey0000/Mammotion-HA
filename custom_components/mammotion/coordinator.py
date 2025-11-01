"""Provides the mammotion DataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import datetime
import json
from abc import abstractmethod
from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import betterproto2
from homeassistant.components import bluetooth
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from mashumaro.exceptions import InvalidFieldValue
from pymammotion.aliyun.cloud_gateway import (
    DeviceOfflineException,
    FailedRequestException,
    GatewayTimeoutException,
    NoConnectionException,
    SetupException,
)
from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.data.model import GenerateRouteInformation, HashList
from pymammotion.data.model.device import MowerInfo, MowingDevice, RTKDevice
from pymammotion.data.model.device_config import OperationSettings, create_path_order
from pymammotion.data.model.report_info import Maintain
from pymammotion.data.mqtt.event import DeviceNotificationEventParams, ThingEventMessage
from pymammotion.data.mqtt.properties import OTAProgressItems, ThingPropertiesMessage
from pymammotion.data.mqtt.status import ThingStatusMessage
from pymammotion.http.model.camera_stream import (
    StreamSubscriptionResponse,
)
from pymammotion.http.model.http import CheckDeviceVersion, ErrorInfo, Response
from pymammotion.http.model.rtk import RTK
from pymammotion.mammotion.commands.mammotion_command import MammotionCommand
from pymammotion.mammotion.devices import MammotionMowerDeviceManager
from pymammotion.mammotion.devices.mammotion import (
    ConnectionPreference,
    Mammotion,
)
from pymammotion.mammotion.devices.mammotion_cloud import MammotionCloud
from pymammotion.proto import RptAct, RptInfoType, SystemUpdateBufMsg
from pymammotion.utility.constant import WorkMode
from pymammotion.utility.device_type import DeviceType

from .config import MammotionConfigStore
from .const import (
    COMMAND_EXCEPTIONS,
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_AUTH_DATA,
    CONF_CONNECT_DATA,
    CONF_DEVICE_DATA,
    CONF_MAMMOTION_DATA,
    CONF_MAMMOTION_DEVICE_LIST,
    CONF_MAMMOTION_DEVICE_RECORDS,
    CONF_MAMMOTION_JWT_INFO,
    CONF_MAMMOTION_MQTT,
    CONF_REGION_DATA,
    CONF_SESSION_DATA,
    DOMAIN,
    EXPIRED_CREDENTIAL_EXCEPTIONS,
    LOGGER,
    NO_REQUEST_MODES,
)

if TYPE_CHECKING:
    from . import MammotionConfigEntry


MAINTENANCE_INTERVAL = timedelta(minutes=60)
DEFAULT_INTERVAL = timedelta(minutes=1)
WORKING_INTERVAL = timedelta(seconds=5)
REPORT_INTERVAL = timedelta(minutes=1)
DEVICE_VERSION_INTERVAL = timedelta(days=1)
MAP_INTERVAL = timedelta(minutes=30)
RTK_INTERVAL = timedelta(hours=5)


class MammotionBaseUpdateCoordinator[DataT](DataUpdateCoordinator[DataT]):
    """Mammotion DataUpdateCoordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: Mammotion,
        update_interval: timedelta,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
            config_entry=config_entry,
        )
        assert config_entry.unique_id
        self.account = self.config_entry.data[CONF_ACCOUNTNAME]
        self.password = self.config_entry.data[CONF_PASSWORD]
        self.device: Device = device
        self.device_name = device.device_name
        self.manager: Mammotion = mammotion
        self._operation_settings = OperationSettings()
        self.update_failures = 0
        self._stream_data: Response[StreamSubscriptionResponse] | None = (
            None  # Stream data [Agora]
        )
        self.commands = MammotionCommand(
            device.device_name,
            int(
                config_entry.data[CONF_MAMMOTION_DATA].data.userInformation.userAccount
            ),
        )

    @abstractmethod
    def get_coordinator_data(self, device: MammotionMowerDeviceManager) -> DataT:
        """Get coordinator data."""

    def set_stream_data(
        self, stream_data: Response[StreamSubscriptionResponse]
    ) -> None:
        """Set stream data."""
        self._stream_data = stream_data

    def get_stream_data(self) -> Response[StreamSubscriptionResponse]:
        """Return stream data."""
        return self._stream_data

    async def join_webrtc_channel(self) -> None:
        """Start stream command."""
        device = self.manager.get_device_by_name(self.device_name)
        command = self.commands.device_agora_join_channel_with_position(enter_state=1)
        await self.async_send_cloud_command(device.iot_id, command)

    async def leave_webrtc_channel(self) -> None:
        """End stream command."""
        device = self.manager.get_device_by_name(self.device_name)
        command = self.commands.device_agora_join_channel_with_position(enter_state=0)
        await self.async_send_cloud_command(device.iot_id, command)

    async def set_scheduled_updates(self, enabled: bool) -> None:
        device = self.manager.get_device_by_name(self.device_name)
        device.state.enabled = enabled
        if device.state.enabled:
            self.update_failures = 0
            if not device.state.online:
                device.state.online = True
            if device.cloud and device.cloud.stopped:
                await device.cloud.start()
        else:
            if device.cloud:
                await device.cloud.stop()
                if device.cloud.mqtt.is_connected():
                    device.cloud.mqtt.disconnect()
            if device.ble:
                await device.ble.stop()

    def is_online(self) -> bool:
        if device := self.manager.get_device_by_name(self.device_name):
            ble = device.ble
            return device.state.online or ble is not None and ble.client.is_connected
        return False

    async def async_refresh_login(self) -> None:
        """Refresh login credentials asynchronously."""
        await self.manager.refresh_login(self.account)
        self.store_cloud_credentials()

    async def device_offline(self, device: MammotionMowerDeviceManager) -> None:
        device.state.online = False
        # if cloud := device.cloud:
        #     await cloud.stop()

        loop = asyncio.get_running_loop()
        loop.call_later(900, lambda: asyncio.create_task(self.clear_update_failures()))

    def store_cloud_credentials(self) -> None:
        """Store cloud credentials in config entry."""
        # config_updates = {}

        if config_entry := self.config_entry:
            account = config_entry.data.get(CONF_ACCOUNTNAME, "")
            mammotion_cloud = (
                self.manager.mqtt_list.get(f"{account}_aliyun")
                if self.manager.mqtt_list.get(f"{account}_aliyun")
                else self.manager.mqtt_list.get(f"{account}_mammotion")
            )

            cloud_client = mammotion_cloud.cloud_client if mammotion_cloud else None

            if cloud_client is not None:
                config_updates = {
                    **config_entry.data,
                    CONF_CONNECT_DATA: cloud_client.connect_response,
                    CONF_AUTH_DATA: cloud_client.login_by_oauth_response,
                    CONF_REGION_DATA: cloud_client.region_response,
                    CONF_AEP_DATA: cloud_client.aep_response,
                    CONF_SESSION_DATA: cloud_client.session_by_authcode_response,
                    CONF_DEVICE_DATA: cloud_client.devices_by_account_response,
                    CONF_MAMMOTION_DATA: cloud_client.mammotion_http.response,
                    CONF_MAMMOTION_MQTT: cloud_client.mammotion_http.mqtt_credentials,
                    CONF_MAMMOTION_DEVICE_LIST: cloud_client.mammotion_http.device_info,
                    CONF_MAMMOTION_DEVICE_RECORDS: cloud_client.mammotion_http.device_records,
                    CONF_MAMMOTION_JWT_INFO: cloud_client.mammotion_http.jwt_info,
                }
                self.hass.config_entries.async_update_entry(
                    config_entry, data=config_updates
                )

    async def async_send_command(self, command: str, **kwargs: Any) -> bool | None:
        """Send command."""
        if not self.manager.get_device_by_name(self.device_name).state.online:
            return False

        device = self.manager.get_device_by_name(self.device_name)

        try:
            await self.manager.send_command_with_args(
                self.device_name, command, **kwargs
            )
            self.update_failures = 0
            return True
        except FailedRequestException:
            self.update_failures += 1
            if self.update_failures < 5:
                return await self.async_send_command(command, **kwargs)
            return False
        except EXPIRED_CREDENTIAL_EXCEPTIONS:
            self.update_failures += 1
            await self.async_refresh_login()
            if self.update_failures < 5:
                return await self.async_send_command(command, **kwargs)
            return False
        except GatewayTimeoutException as ex:
            LOGGER.error(f"Gateway timeout exception: {ex.iot_id}")
            self.update_failures = 0
            return False
        except (DeviceOfflineException, NoConnectionException) as ex:
            """Device is offline try bluetooth if we have it."""
            try:
                if ble := device.ble:
                    # if we don't do this it will stay connected and no longer update over wifi
                    ble.set_disconnect_strategy(disconnect=True)
                    await ble.queue_command(command, **kwargs)

                    return True
                raise DeviceOfflineException(ex.args[0], self.device.iot_id)
            except COMMAND_EXCEPTIONS as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="command_failed"
                ) from exc

    async def async_send_cloud_command(
        self, iot_id: str, command: bytes
    ) -> bool | None:
        """Send command."""
        if not self.manager.get_device_by_name(self.device_name).state.online:
            return False

        device = self.manager.get_device_by_name(self.device_name)

        try:
            await device.cloud_client.send_cloud_command(iot_id, command)
            self.update_failures = 0
            return True
        except FailedRequestException:
            self.update_failures += 1
            if self.update_failures < 5:
                return await self.async_send_cloud_command(device.iot_id, command)
            return False
        except EXPIRED_CREDENTIAL_EXCEPTIONS:
            self.update_failures += 1
            await self.async_refresh_login()
            if self.update_failures < 5:
                return await self.async_send_cloud_command(device.iot_id, command)
            return False
        except GatewayTimeoutException as ex:
            LOGGER.error(f"Gateway timeout exception: {ex.iot_id}")
            self.update_failures = 0
            return False
        except (DeviceOfflineException, NoConnectionException) as ex:
            """Device is offline try bluetooth if we have it."""
            LOGGER.error(f"Device offline: {ex.iot_id}")
        return False

    async def check_firmware_version(self) -> None:
        """Check if firmware version is updated."""
        if mower := self.manager.mower(self.device_name):
            device_registry = dr.async_get(self.hass)
            device_entry = device_registry.async_get_device(
                identifiers={(DOMAIN, self.device_name)}
            )
            if device_entry is None:
                return

            new_swversion = mower.device_firmwares.device_version

            if new_swversion is not None or new_swversion != device_entry.sw_version:
                device_registry.async_update_device(
                    device_entry.id, sw_version=new_swversion
                )

            if model_id := mower.mower_state.model_id:
                if model_id is not None or model_id != device_entry.model_id:
                    device_registry.async_update_device(
                        device_entry.id, model_id=model_id
                    )

    async def update_firmware(self, version: str) -> None:
        """Update firmware."""
        device = self.manager.get_device_by_name(self.device_name)
        await device.mammotion_http.start_ota_upgrade(device.iot_id, version)

    async def async_sync_maps(self) -> None:
        """Get map data from the device."""
        try:
            self.clear_all_maps()
            await self.manager.start_map_sync(self.device_name)
        except EXPIRED_CREDENTIAL_EXCEPTIONS:
            self.update_failures += 1
            await self.async_refresh_login()
            if self.update_failures < 5:
                await self.async_sync_maps()

    async def async_start_stop_blades(
        self, start_stop: bool, blade_height: int = 60
    ) -> None:
        """Start stop blades."""
        if DeviceType.is_luba1(self.device_name):
            if start_stop:
                await self.async_send_command("set_blade_control", on_off=1)
            else:
                await self.async_send_command("set_blade_control", on_off=0)
        elif start_stop:
            if DeviceType.is_yuka(self.device_name) or DeviceType.is_yuka_mini(
                self.device_name
            ):
                blade_height = 0

            await self.async_send_command(
                "operate_on_device",
                main_ctrl=1,
                cut_knife_ctrl=1,
                cut_knife_height=blade_height,
                max_run_speed=1.2,
            )
        else:
            await self.async_send_command(
                "operate_on_device",
                main_ctrl=0,
                cut_knife_ctrl=0,
                cut_knife_height=blade_height,
                max_run_speed=1.2,
            )

    async def async_set_rain_detection(self, on_off: bool) -> None:
        """Set rain detection."""
        await self.async_send_command(
            "read_write_device", rw_id=3, context=int(on_off), rw=1
        )

    async def async_read_rain_detection(self) -> None:
        """Set rain detection."""
        await self.async_send_command("read_write_device", rw_id=3, context=1, rw=0)

    async def async_set_sidelight(self, on_off: int) -> None:
        """Set Sidelight."""
        await self.async_send_command(
            "read_and_set_sidelight", is_sidelight=bool(on_off), operate=0
        )
        await self.async_read_sidelight()

    async def async_read_sidelight(self) -> None:
        """Set Sidelight."""
        await self.async_send_command(
            "read_and_set_sidelight", is_sidelight=False, operate=1
        )

    async def async_set_manual_light(self, manual_ctrl: bool) -> None:
        """Set manual night light."""
        await self.async_send_command("set_car_manual_light", manual_ctrl=manual_ctrl)
        await self.async_send_command("get_car_light", ids=1126)

    async def async_set_night_light(self, night_light: bool) -> None:
        """Set night light."""
        await self.async_send_command("set_car_light", on_off=night_light)
        await self.async_send_command("get_car_light", ids=1123)

    async def async_set_traversal_mode(self, context: int) -> None:
        """Set traversal mode."""
        await self.async_send_command("traverse_mode", context=context)

    async def async_set_turning_mode(self, context: int) -> None:
        """Set turning mode."""
        await self.async_send_command("turning_mode", context=context)

    async def async_blade_height(self, height: int) -> int:
        """Set blade height."""
        await self.async_send_command("set_blade_height", height=height)
        return height

    async def async_set_cutter_speed(self, mode: int) -> None:
        """Set cutter speed."""
        await self.async_send_command("set_cutter_mode", cutter_mode=mode)

    async def async_set_speed(self, speed: float) -> None:
        """Set working speed."""
        await self.async_send_command("set_speed", speed=speed)

    async def async_leave_dock(self) -> None:
        """Leave dock."""
        await self.send_command_and_update("leave_dock")

    async def async_cancel_task(self) -> None:
        """Cancel task."""
        await self.send_command_and_update("cancel_job")

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
        await self.async_send_command("read_write_device", rw_id=5, rw=1, context=1)

    async def async_get_area_list(self) -> None:
        """Mowing area List."""
        await self.async_send_command(
            "get_area_name_list", device_id=self.device.iot_id
        )

    async def async_relocate_charging_station(self):
        """Reset charging station."""
        await self.async_send_command("delete_charge_point")
        # fetch charging location?
        """
        nav {
          todev_get_commondata {
            pver: 1
            subCmd: 2
            action: 6
            type: 5
            totalFrame: 1
            currentFrame: 1
          }
        }
        """

    async def send_command_and_update(self, command_str: str, **kwargs: Any) -> None:
        """Send command and update."""
        await self.async_send_command(command_str, **kwargs)
        await self.async_request_iot_sync()

    async def async_request_iot_sync(self, stop: bool = False) -> None:
        """Sync specific info from device."""
        await self.async_send_command(
            "request_iot_sys",
            rpt_act=RptAct.RPT_STOP if stop else RptAct.RPT_START,
            rpt_info_type=[
                RptInfoType.RIT_DEV_STA,
                RptInfoType.RIT_DEV_LOCAL,
                RptInfoType.RIT_WORK,
                RptInfoType.RIT_MAINTAIN,
                RptInfoType.RIT_BASESTATION_INFO,
                RptInfoType.RIT_VIO,
            ],
            timeout=10000,
            period=3000,
            no_change_period=4000,
            count=0,
        )

    async def async_plan_route(
        self, operation_settings: OperationSettings
    ) -> bool | None:
        """Plan mow."""

        if self.data.report_data.dev:
            dev = self.data.report_data.dev
            if dev.collector_status.collector_installation_status == 0:
                operation_settings.is_dump = False

        if DeviceType.is_yuka(self.device_name):
            operation_settings.blade_height = -10

        route_information = GenerateRouteInformation(
            one_hashs=list(operation_settings.areas),
            rain_tactics=operation_settings.rain_tactics,
            speed=operation_settings.speed,
            ultra_wave=operation_settings.ultra_wave,  # touch no touch etc
            toward=operation_settings.toward,  # is just angle (route angle)
            toward_included_angle=operation_settings.toward_included_angle  # demond_angle
            if operation_settings.channel_mode == 1
            else 0,  # crossing angle relative to grid
            toward_mode=operation_settings.toward_mode,
            blade_height=operation_settings.blade_height,
            channel_mode=operation_settings.channel_mode,  # single, double, segment or none (route mode)
            channel_width=operation_settings.channel_width,  # path space
            job_mode=operation_settings.job_mode,  # taskMode grid or border first
            edge_mode=operation_settings.mowing_laps,  # perimeter/mowing laps
            path_order=create_path_order(operation_settings, self.device_name),
            obstacle_laps=operation_settings.obstacle_laps,
        )

        if DeviceType.is_luba1(self.device_name):
            route_information.toward_mode = 0
            route_information.toward_included_angle = 0

        # not sure if this is artificial limit
        # if (
        #     DeviceType.is_mini_or_x_series(self.device_name)
        #     and route_information.toward_mode == 0
        # ):
        #     route_information.toward = 0

        return await self.async_send_command(
            "generate_route_information", generate_route_information=route_information
        )

    async def start_task(self, plan_id: str) -> None:
        """Start task."""
        await self.async_send_command("single_schedule", plan_id=plan_id)

    def clear_all_maps(self) -> None:
        """Clear all map data stored."""
        data = self.manager.get_device_by_name(self.device_name).state
        data.map = HashList()

    async def clear_update_failures(self) -> None:
        """Clear update failures."""
        self.update_failures = 0
        device = self.manager.get_device_by_name(self.device_name)
        if not device.state.online:
            device.state.online = True
        if cloud := device.cloud:
            if cloud.stopped:
                await cloud.start()

    @property
    def operation_settings(self) -> OperationSettings:
        """Return operation settings for planning."""
        return self._operation_settings

    async def async_restore_data(self) -> None:
        """Restore saved data."""
        store = MammotionConfigStore(
            self.hass, version=1, minor_version=2, key=self.device_name
        )
        restored_data: Mapping[str, Any] | None = await store.async_load()

        if restored_data is None:
            self.data = MowingDevice()
            self.manager.get_device_by_name(self.device_name).state = self.data
            return

        try:
            if restored_data is not None:
                mower_state = MowingDevice().from_dict(restored_data)
                if device := self.manager.get_device_by_name(self.device_name):
                    device.state = mower_state
        except InvalidFieldValue:
            """invalid"""
            self.data = MowingDevice()
            self.manager.get_device_by_name(self.device_name).state = self.data

    async def async_save_data(self, data: MowingDevice) -> None:
        """Get map data from the device."""
        store = Store(self.hass, version=1, minor_version=2, key=self.device_name)
        await store.async_save(data.to_dict())

    async def _async_update_data(self) -> DataT | None:
        """Update data from the device."""
        if device := self.manager.get_device_by_name(self.device_name):
            if not device.state.enabled or (
                not device.state.online
                and device.preference is ConnectionPreference.WIFI
            ):
                if cloud := device.cloud:
                    if not device.state.enabled and cloud.mqtt.is_connected():
                        cloud.mqtt.disconnect()
                if ble := device.ble:
                    if not device.state.enabled:
                        if ble.client is not None and ble.client.is_connected:
                            await ble.client.disconnect()
                return self.get_coordinator_data(device)

            if (
                device.state.mower_state.ble_mac != ""
                and device.preference is ConnectionPreference.BLUETOOTH
            ):
                if ble_device := bluetooth.async_ble_device_from_address(
                    self.hass, device.state.mower_state.ble_mac.upper(), True
                ):
                    if ble := device.ble:
                        ble.update_device(ble_device)
                    else:
                        device.add_ble(ble_device)

            # don't query the mower while users are doing map changes or its updating.
            if device.state.report_data.dev.sys_status in NO_REQUEST_MODES:
                # MQTT we are likely to get an update, BLE we are not
                if device.preference is ConnectionPreference.BLUETOOTH:
                    loop = asyncio.get_running_loop()
                    loop.call_later(
                        300,
                        lambda: asyncio.create_task(
                            self.async_send_command("get_report_cfg")
                        ),
                    )
                return self.get_coordinator_data(device)

            if (
                self.update_failures > 5
                and device.preference is ConnectionPreference.WIFI
            ):
                """Don't hammer the mammotion/ali servers"""
                loop = asyncio.get_running_loop()
                loop.call_later(
                    60, lambda: asyncio.create_task(self.clear_update_failures())
                )

                return self.get_coordinator_data(device)

            # last_sent_times = []
            # if cloud := device.cloud:
            #     last_sent_times.append(cloud.command_sent_time)
            # if ble := device.ble:
            #     last_sent_times.append(ble.command_sent_time)
            #
            # seconds_check = (
            #     self.update_interval.seconds
            #     if self.update_interval != WORKING_INTERVAL
            #     else DEFAULT_INTERVAL
            # )
            #
            # if self.update_interval and any(
            #     t > time.time() - seconds_check for t in last_sent_times
            # ):
            #     return self.get_coordinator_data(device)

            return None
        return None

    async def _async_update_notification(self, res: tuple[str, Any | None]) -> None:
        """Update data from incoming messages."""

    async def _async_update_properties(
        self, properties: ThingPropertiesMessage
    ) -> None:
        """Update data from incoming properties messages."""

    async def _async_update_status(self, status: ThingStatusMessage) -> None:
        """Update data from incoming status messages."""

    async def _async_update_event_message(self, event: ThingEventMessage) -> None:
        """Update data from incoming event messages."""

    async def _async_setup(self) -> None:
        device = self.manager.get_device_by_name(self.device_name)

        if self.data is None:
            self.data = device.state
        if cloud := device.cloud:
            cloud.set_notification_callback(self._async_update_notification)
        elif ble := device.ble:
            ble.set_notification_callback(self._async_update_notification)

        device.state_manager.properties_callback.add_subscribers(
            self._async_update_properties
        )
        device.state_manager.status_callback.add_subscribers(self._async_update_status)

        device.state_manager.device_event_callback.add_subscribers(
            self._async_update_event_message
        )

    async def find_entity_by_attribute_in_registry(
        self, attribute_name, attribute_value
    ):
        """Find an entity using the entity registry based on attributes."""
        entity_registry = await self.hass.helpers.entity_registry.async_get_registry()

        for entity_id, entity_entry in entity_registry.entities.items():
            entity_state = self.hass.states.get(entity_id)
            if (
                entity_state
                and entity_state.attributes.get(attribute_name) == attribute_value
            ):
                return entity_id, entity_entry

        return None, None

    def get_area_entity_name(self, area_hash: int) -> str | None:
        """Get string name of area hash."""
        if area_hash == 0:
            return None

        name = None
        if area_data := self.data.map.area.get(area_hash):
            area_frame = area_data.data[0] if len(area_data.data) > 0 else None
            if area_frame is not None and area_frame.area_label.label != "":
                name = area_frame.area_label.label
        else:
            LOGGER.error("area not found %s %s", self.device_name, area_hash)
            return None

        return name if name else f"area {area_hash}"


class MammotionReportUpdateCoordinator(MammotionBaseUpdateCoordinator[MowingDevice]):
    """Mammotion report update coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: Mammotion,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=REPORT_INTERVAL,
        )

    def get_coordinator_data(self, device: MammotionMowerDeviceManager) -> MowingDevice:
        """Get coordinator data."""
        return device.state

    async def _async_update_data(self) -> MowingDevice:
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data

        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            LOGGER.debug("device not found")
            return data

        try:
            await self.async_send_command("get_report_cfg")

        except DeviceOfflineException as ex:
            """Device is offline."""
            if ex.iot_id == self.device.iot_id:
                device = self.manager.get_device_by_name(self.device_name)
                await self.device_offline(device)
                return device.state

        LOGGER.debug("Updated Mammotion device %s", self.device_name)
        LOGGER.debug("================= Debug Log =================")
        if device.preference is ConnectionPreference.BLUETOOTH:
            if device.ble:
                LOGGER.debug(
                    "Mammotion device data: %s",
                    device.ble._raw_data,
                )
        if device.preference is ConnectionPreference.WIFI:
            if device.cloud:
                LOGGER.debug(
                    "Mammotion device data: %s",
                    device.cloud._raw_data,
                )
        LOGGER.debug("==================================")

        self.update_failures = 0
        data = self.manager.get_device_by_name(self.device_name).state
        await self.async_save_data(data)

        if data.report_data.dev.sys_status in (
            WorkMode.MODE_WORKING,
            WorkMode.MODE_RETURNING,
        ):
            self.update_interval = WORKING_INTERVAL
        else:
            self.update_interval = DEFAULT_INTERVAL

        return data

    # TODO filter by device
    async def _async_update_notification(self, res: tuple[str, Any | None]) -> None:
        """Update data from incoming messages."""
        if res[0] == "sys" and res[1] is not None:
            sys_msg = betterproto2.which_one_of(res[1], "SubSysMsg")
            if sys_msg[0] == "toapp_report_data":
                if mower := self.manager.mower(self.device_name):
                    self.async_set_updated_data(mower)

    async def _async_update_properties(
        self, properties: ThingPropertiesMessage
    ) -> None:
        """Update data from incoming properties messages."""
        if not self.data.online and self.data.enabled:
            await self.set_scheduled_updates(True)
            if mower := self.manager.mower(self.device_name):
                self.async_set_updated_data(mower)

    async def _async_update_status(self, status: ThingStatusMessage) -> None:
        """Update data from incoming status messages."""
        if not self.data.online and self.data.enabled:
            await self.set_scheduled_updates(True)
            if mower := self.manager.mower(self.device_name):
                self.async_set_updated_data(mower)

    async def _async_update_event_message(self, event: ThingEventMessage) -> None:
        """Update data from incoming event messages."""
        if not self.data.online and self.data.enabled:
            await self.set_scheduled_updates(True)
            if mower := self.manager.mower(self.device_name):
                self.async_set_updated_data(mower)


class MammotionMaintenanceUpdateCoordinator(MammotionBaseUpdateCoordinator[Maintain]):
    """Class to manage fetching mammotion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: Mammotion,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=MAINTENANCE_INTERVAL,
        )

    def get_coordinator_data(self, device: MammotionMowerDeviceManager) -> Maintain:
        """Get coordinator data."""
        return device.state.report_data.maintenance

    async def _async_update_data(self) -> Maintain:
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data

        try:
            await self.async_send_command("get_maintenance")

        except DeviceOfflineException as ex:
            """Device is offline try bluetooth if we have it."""
            if ex.iot_id == self.device.iot_id:
                device = self.manager.get_device_by_name(self.device_name)
                await self.device_offline(device)
                return device.state
        except GatewayTimeoutException:
            """Gateway is timing out again."""

        return self.manager.get_device_by_name(
            self.device.device_name
        ).state.report_data.maintenance

    async def _async_setup(self) -> None:
        """Setup maintenance coordinator."""
        await super()._async_setup()
        device = self.manager.get_device_by_name(self.device_name)
        if self.data is None:
            self.data = device.state.report_data.maintenance


class MammotionDeviceVersionUpdateCoordinator(
    MammotionBaseUpdateCoordinator[MowingDevice]
):
    """Class to manage fetching mammotion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: Mammotion,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=DEFAULT_INTERVAL,
        )

    def get_coordinator_data(self, device: MammotionMowerDeviceManager) -> MowingDevice:
        """Get coordinator data."""
        return device.state

    async def _async_update_properties(
        self, properties: ThingPropertiesMessage
    ) -> None:
        """Update data from incoming properties messages."""
        if ota_progress := properties.params.items.otaProgress:
            ota_progress.value = OTAProgressItems.from_dict(ota_progress.value)
            self.data.update_check.progress = ota_progress.value.progress
            self.data.update_check.isupgrading = True
            if ota_progress.value.progress == 100:
                self.data.update_check.isupgrading = False
                self.data.update_check.upgradeable = False
                self.data.device_firmwares.device_version = ota_progress.value.version
            self.async_set_updated_data(self.data)

    async def _async_update_data(self):
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data
        device = self.manager.get_device_by_name(self.device_name)
        command_list = [
            "get_device_version_main",
            "get_device_version_info",
            "get_device_base_info",
            "get_device_product_model",
        ]
        for command in command_list:
            try:
                await self.async_send_command(command)

            except DeviceOfflineException as ex:
                """Device is offline bluetooth has been attempted."""
                if ex.iot_id == self.device.iot_id:
                    await self.device_offline(device)
                    return device.state
            except GatewayTimeoutException:
                """Gateway is timing out again."""

        await self.check_firmware_version()

        ota_info = await device.mammotion_http.get_device_ota_firmware([device.iot_id])
        LOGGER.debug("OTA info: %s", ota_info.data)
        if check_versions := ota_info.data:
            for check_version in check_versions:
                if check_version.device_id == device.iot_id:
                    device.state.update_check = check_version

        if device.state.mower_state.model_id != "":
            self.update_interval = DEVICE_VERSION_INTERVAL

        return device.state

    async def _async_setup(self) -> None:
        """Setup device version coordinator."""
        await super()._async_setup()
        device = self.manager.get_device_by_name(self.device_name)
        if self.data is None:
            self.data = device.state

        try:
            if device.state.mower_state.model_id == "":
                await self.async_send_command("get_device_product_model")
            if device.state.mower_state.wifi_mac == "":
                await self.async_send_command("get_device_network_info")

            ota_info = await device.mammotion_http.get_device_ota_firmware(
                [device.iot_id]
            )
            if check_versions := ota_info.data:
                for check_version in check_versions:
                    if check_version.device_id == device.iot_id:
                        device.state.update_check = check_version

            self.async_set_updated_data(self.data)
        except DeviceOfflineException:
            """Device is offline bluetooth has been attempted."""


class MammotionMapUpdateCoordinator(MammotionBaseUpdateCoordinator[MowerInfo]):
    """Class to manage fetching mammotion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: Mammotion,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=MAP_INTERVAL,
        )

    def get_coordinator_data(self, device: MammotionMowerDeviceManager) -> MowerInfo:
        """Get coordinator data."""
        return device.state.mower_state

    def _map_callback(self) -> None:
        """Trigger a resync when the bol hash changes."""
        # TODO setup callback to get bol hash data

    async def _async_update_data(self):
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data
        device = self.manager.get_device_by_name(self.device_name)

        try:
            if (
                round(device.state.location.RTK.latitude, 0) == 0
                or round(device.state.location.dock.latitude, 0) == 0
            ):
                await self.async_rtk_dock_location()

            if (
                len(device.state.map.hashlist) == 0
                or len(device.state.map.missing_hashlist()) > 0
            ):
                await self.manager.start_map_sync(self.device_name)

        except DeviceOfflineException as ex:
            """Device is offline try bluetooth if we have it."""
            if ex.iot_id == self.device.iot_id:
                await self.device_offline(device)
                return device.state.mower_state
        except GatewayTimeoutException:
            """Gateway is timing out again."""

        return self.manager.get_device_by_name(self.device_name).state.mower_state

    async def _async_setup(self) -> None:
        """Setup coordinator with initial call to get map data."""
        await super()._async_setup()
        device = self.manager.get_device_by_name(self.device_name)
        if self.data is None:
            self.data = device.state.mower_state

        if not device.state.enabled or not device.state.online:
            return
        try:
            await self.async_rtk_dock_location()
            if not DeviceType.is_luba1(self.device_name):
                await self.async_get_area_list()
        except DeviceOfflineException as ex:
            """Device is offline try bluetooth if we have it."""
            if ex.iot_id == self.device.iot_id:
                await self.device_offline(device)
        except GatewayTimeoutException:
            """Gateway is timing out again."""


class MammotionDeviceErrorUpdateCoordinator(
    MammotionBaseUpdateCoordinator[MowingDevice]
):
    """Class to manage fetching mammotion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: Mammotion,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=DEFAULT_INTERVAL,
        )

    def get_coordinator_data(self, device: MammotionMowerDeviceManager) -> MowingDevice:
        """Get coordinator data."""
        return device.state

    async def _async_update_event_message(self, event: ThingEventMessage) -> None:
        if (
            hasattr(event.params, "identifier")
            and event.params.identifier == "device_warning_code_event"
        ):
            event_params: DeviceNotificationEventParams = event.params
            # '[{"c":-2801,"ct":1,"ft":1731493734000},{"c":-1008,"ct":1,"ft":1731493734000}]'
            try:
                warning_event = json.loads(event_params.value.data)
                LOGGER.debug("warning event %s", warning_event)
                await self._async_update_data()
                if mower := self.manager.mower(self.device_name):
                    self.async_set_updated_data(mower)
            except json.JSONDecodeError:
                """Failed to parse warning event."""

    async def _async_update_notification(self, res: tuple[str, Any | None]) -> None:
        """Update data from incoming notifications messages."""
        if res[0] == "sys" and res[1] is not None:
            sys_msg = betterproto2.which_one_of(res[1], "SubSysMsg")
            if sys_msg[0] == "system_update_buf" and sys_msg[1] is not None:
                buffer_list: SystemUpdateBufMsg = sys_msg[1]
                if buffer_list.update_buf_data[0] == 2:
                    if mower := self.manager.mower(self.device_name):
                        self.async_set_updated_data(mower)

    def get_error_code(self, number: int) -> int:
        """Get error code from an error code list."""
        try:
            return abs(next(iter(self.data.errors.err_code_list)))
        except StopIteration:
            return 0

    def get_error_time(self, number: int) -> datetime.datetime | None:
        """Get error time from an error code list."""
        try:
            return datetime.datetime.fromtimestamp(
                next(iter(self.data.errors.err_code_list_time)), datetime.UTC
            )
        except StopIteration:
            return None

    def get_error_message(self, number: int) -> str:
        """Return error message."""
        try:
            error_code: int = next(iter(self.data.errors.err_code_list))

            error_code = abs(error_code)
            error_info: ErrorInfo = self.data.errors.error_codes[f"{error_code}"]

            implication = (
                getattr(error_info, f"{self.hass.config.language}_implication")
                if hasattr(error_info, f"{self.hass.config.language}_implication")
                else error_info.en_implication
            )
            solution = (
                getattr(error_info, f"{self.hass.config.language}_solution")
                if hasattr(error_info, f"{self.hass.config.language}_solution")
                else error_info.en_solution
            )

            if implication == "":
                implication = error_info.en_implication

            if solution == "":
                solution = error_info.en_solution

            return f"{error_info.module}: {implication}, {solution}"

        except StopIteration:
            """Failed to get error code."""
            return "No Error"
        except KeyError:
            """Failed to get error message."""
            return "Error message not found"

    async def _async_update_data(self):
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data
        device = self.manager.get_device_by_name(self.device_name)

        try:
            await self.async_send_command("read_write_device", rw_id=5, rw=1, context=2)
            await self.async_send_command("read_write_device", rw_id=5, rw=1, context=3)
            if not device.state.errors.error_codes:
                device.state.errors.error_codes = (
                    await device.mammotion_http.get_all_error_codes()
                )
        except DeviceOfflineException as ex:
            """Device is offline bluetooth has been attempted."""
            if ex.iot_id == self.device.iot_id:
                await self.device_offline(device)
                return device.state
        except GatewayTimeoutException:
            """Gateway is timing out again."""

        return device.state

    async def _async_setup(self) -> None:
        """Setup device version coordinator."""
        await super()._async_setup()
        device = self.manager.get_device_by_name(self.device_name)
        if self.data is None:
            self.data = device.state

        try:
            # get current errors
            await self.async_send_command("read_write_device", rw_id=5, rw=1, context=2)
            await self.async_send_command("read_write_device", rw_id=5, rw=1, context=3)
            if not device.state.errors.error_codes:
                device.state.errors.error_codes = (
                    await device.mammotion_http.get_all_error_codes()
                )

            self.async_set_updated_data(self.data)
        except DeviceOfflineException:
            """Device is offline bluetooth has been attempted."""


class MammotionRTKCoordinator(DataUpdateCoordinator[RTKDevice]):
    """Mammotion DataUpdateCoordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        cloud: MammotionCloud,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=RTK_INTERVAL,
            config_entry=config_entry,
        )
        assert config_entry.unique_id
        self.account = self.config_entry.data[CONF_ACCOUNTNAME]
        self.password = self.config_entry.data[CONF_PASSWORD]
        self.device: Device = device
        self.device_name = device.device_name
        self.cloud: MammotionCloud = cloud
        self.data: RTKDevice = RTKDevice(
            name=self.device_name,
            iot_id=self.device.iot_id,
            product_key=self.device.product_key,
        )
        self.cloud.mqtt_message_event.add_subscribers(self._on_mqtt_message)
        self.cloud.mqtt_properties_event.add_subscribers(self._on_mqtt_properties)

    async def _on_mqtt_message(self, event: ThingEventMessage) -> None:
        if event.params.iotId != self.data.iot_id:
            return
        # set data based on mqtt protobuf messages to set lat lon

    async def _on_mqtt_properties(self, properties: ThingPropertiesMessage) -> None:
        if properties.params.iotId != self.data.iot_id:
            return
        if ota_progress := properties.params.items.otaProgress:
            ota_progress.value = OTAProgressItems.from_dict(ota_progress.value)
            self.data.update_check.progress = ota_progress.value.progress
            self.data.update_check.isupgrading = True
            if ota_progress.value.progress == 100:
                self.data.update_check.isupgrading = False
                self.data.update_check.upgradeable = False
                self.data.device_version = ota_progress.value.version
            self.async_set_updated_data(self.data)

    async def _async_update_data(self):
        """Update RTK data."""
        try:
            response = await self.cloud.cloud_client.get_device_properties(
                self.device.iot_id
            )
            if response.code == 200:
                data = response.data
                if ota_progress := data.otaProgress:
                    self.data.update_check = CheckDeviceVersion.from_dict(
                        ota_progress.value
                    )
                if network_info := data.networkInfo:
                    network = json.loads(network_info.value)
                    self.data.wifi_rssi = network["wifi_rssi"]
                    self.data.wifi_sta_mac = network["wifi_sta_mac"]
                    self.data.bt_mac = network["bt_mac"]
                if coordinate := data.coordinate:
                    coord_val = json.loads(coordinate.value)
                    if self.data.lat == 0:
                        self.data.lat = coord_val["lat"]
                    if self.data.lon == 0:
                        self.data.lon = coord_val["lon"]
                if device_version := data.deviceVersion:
                    self.data.device_version = device_version.value
            self.data.online = True

            ota_info = (
                await self.cloud.cloud_client.mammotion_http.get_device_ota_firmware(
                    [self.data.iot_id]
                )
            )
            if check_versions := ota_info.data:
                for check_version in check_versions:
                    if check_version.device_id == self.data.iot_id:
                        self.data.update_check = check_version
            return self.data
        except SetupException:
            """Cloud IOT Gateway is not setup."""
            return self.data
        except DeviceOfflineException:
            self.data.online = False
        except GatewayTimeoutException:
            """Gateway is timing out again."""
        return self.data

    async def _async_setup(self) -> None:
        """Setup RTK data."""

        rtk_response = await self.cloud.cloud_client.mammotion_http.get_rtk_devices()
        if rtk_response.code == 0:
            rtk_list = [
                rtk
                for rtk in rtk_response.data
                if self.device.device_name == rtk.device_name
            ]
            try:
                rtk_device: RTK = next(iter(rtk_list))
                self.data.lora_version = rtk_device.lora
            except StopIteration:
                """Failed to get RTK device."""
                return

    async def update_firmware(self, version: str) -> None:
        """Update firmware."""
        await self.cloud.cloud_client.mammotion_http.start_ota_upgrade(
            self.device.iot_id, version
        )
