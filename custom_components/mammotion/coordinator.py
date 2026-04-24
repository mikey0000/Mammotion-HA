"""Provides the mammotion DataUpdateCoordinator."""

from __future__ import annotations

import datetime
import json
from abc import abstractmethod
from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

import betterproto2
from homeassistant.components import bluetooth
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from mashumaro.exceptions import InvalidFieldValue
from pymammotion.aliyun.exceptions import (
    DeviceOfflineException,
    FailedRequestException,
    GatewayTimeoutException,
    NoConnectionException,
    TooManyRequestsException,
)
from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.client import MammotionClient
from pymammotion.data.model import GenerateRouteInformation
from pymammotion.data.model.device import (
    MowerDevice,
    MowerInfo,
    MowingDevice,
    RTKBaseStationDevice,
)
from pymammotion.data.model.device_config import OperationSettings, create_path_order
from pymammotion.data.model.hash_list import AreaHashNameList, SvgMessage
from pymammotion.data.model.report_info import Maintain
from pymammotion.data.mqtt.event import DeviceNotificationEventParams, ThingEventMessage
from pymammotion.data.mqtt.properties import ThingPropertiesMessage
from pymammotion.data.mqtt.status import StatusType, ThingStatusMessage
from pymammotion.http.model.camera_stream import (
    StreamSubscriptionResponse,
)
from pymammotion.http.model.http import ErrorInfo, Response
from pymammotion.mammotion.commands.mammotion_command import MammotionCommand
from pymammotion.proto import RptAct, RptInfoType, SystemUpdateBufMsg
from pymammotion.state.device_state import DeviceSnapshot
from pymammotion.transport.base import (
    AuthError,
    CommandTimeoutError,
    ConcurrentRequestError,
    LoginFailedError,
    NoTransportAvailableError,
    ReLoginRequiredError,
    SessionExpiredError,
    Subscription,
    TransportType,
)
from pymammotion.utility.constant import WorkMode
from pymammotion.utility.device_type import DeviceType
from webrtc_models import RTCIceServer

from .agora_api import SERVICE_IDS, AgoraAPIClient, AgoraResponse
from .config import MammotionConfigStore
from .const import (
    COMMAND_EXCEPTIONS,
    CONF_ACCOUNTNAME,
    CONF_CONNECT_DATA,
    CONF_MAMMOTION_DATA,
    DOMAIN,
    EXPIRED_CREDENTIAL_EXCEPTIONS,
    LOGGER,
    NO_REQUEST_MODES,
)

if TYPE_CHECKING:
    from . import MammotionConfigEntry


MAINTENANCE_INTERVAL = timedelta(minutes=60)
DEFAULT_INTERVAL = timedelta(minutes=30)
REPORT_INTERVAL = timedelta(minutes=30)
DEVICE_VERSION_INTERVAL = timedelta(weeks=1)
MAP_INTERVAL = timedelta(minutes=30)
RTK_INTERVAL = timedelta(hours=5)


class MammotionBaseUpdateCoordinator[DataT](DataUpdateCoordinator[DataT]):
    """Mammotion DataUpdateCoordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: MammotionClient,
        update_interval: timedelta,
        unique_name: str | None = None,
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
        self.account = self.config_entry.data.get(CONF_ACCOUNTNAME, "")
        self.password = self.config_entry.data.get(CONF_PASSWORD, "")
        self.device: Device = device
        self.device_name = device.device_name
        self.unique_name = (
            unique_name if unique_name is not None else device.device_name
        )
        self.manager: MammotionClient = mammotion
        self._operation_settings = OperationSettings()
        self.update_failures = 0
        self._stream_data: Response[StreamSubscriptionResponse] | None = (
            None  # Stream data [Agora]
        )
        _mammotion_data = config_entry.data.get(CONF_MAMMOTION_DATA) or {}
        try:
            _user_account = int(
                _mammotion_data["data"]["userInformation"]["userAccount"]
            )
        except (KeyError, TypeError, ValueError):
            _user_account = 0
        self.commands = MammotionCommand(device.device_name, _user_account)
        self._subscriptions: list[Subscription] = []

        device = self.manager.get_device_by_name(self.device_name)

        if self.data is None:
            self.data = device

    @abstractmethod
    def get_coordinator_data(self, device: MowingDevice) -> DataT:
        """Get coordinator data."""

    async def async_check_stream_expiry(
        self,
    ) -> tuple[StreamSubscriptionResponse | None, AgoraResponse | None]:
        """Check if stream token is expired and refresh if needed."""
        stream_data = None
        agora_response = None

        try:
            # Refresh stream data
            stream_data = await self.manager.get_stream_subscription(
                self.device_name, self.device.iot_id
            )
            self.set_stream_data(stream_data)

            if stream_data is not None and stream_data.data is not None:
                LOGGER.debug("Received stream data: %s", stream_data)

                # Get ICE servers from Agora API
                try:
                    subscription = stream_data.data.to_dict()
                    async with AgoraAPIClient() as agora_client:
                        agora_response = await agora_client.choose_server(
                            app_id=subscription["appid"],
                            token=subscription["token"],
                            channel_name=subscription["channelName"],
                            user_id=int(subscription["uid"]),
                            service_flags=[
                                SERVICE_IDS["CHOOSE_SERVER"],  # Gateway addresses
                                SERVICE_IDS["CLOUD_PROXY_FALLBACK"],  # TURN servers
                            ],
                        )

                        # Get ICE servers and convert to RTCIceServer format - use only first TURN server to match SDK (3 entries)
                        ice_servers_agora = agora_response.get_ice_servers(
                            use_all_turn_servers=False
                        )
                        LOGGER.info("Ice Servers from Agora API:%s", ice_servers_agora)
                        ice_servers = [
                            RTCIceServer(
                                urls=ice_server.urls,
                                username=ice_server.username,
                                credential=ice_server.credential,
                            )
                            for ice_server in ice_servers_agora
                        ]

                        # Store ICE servers in coordinator
                        self._ice_servers = ice_servers
                        self._agora_response = agora_response
                        LOGGER.info(
                            "Retrieved %d ICE servers from Agora API",
                            len(ice_servers),
                        )
                except Exception as e:
                    LOGGER.error("Failed to get ICE servers from Agora API: %s", e)
                    self._ice_servers = []

            LOGGER.debug("Stream token refreshed successfully")
        except Exception as ex:
            LOGGER.error("Failed to refresh stream token: %s", ex)
        return stream_data, agora_response

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
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return
        command = self.commands.device_agora_join_channel_with_position(enter_state=1)
        await self.async_send_cloud_command(handle.iot_id, command)

    async def leave_webrtc_channel(self) -> None:
        """End stream command."""
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return
        command = self.commands.device_agora_join_channel_with_position(enter_state=0)
        await self.async_send_cloud_command(handle.iot_id, command)

    async def set_scheduled_updates(self, enabled: bool) -> None:
        """Enable or disable scheduled polling updates for this device."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return
        device.enabled = enabled
        if enabled:
            self.update_failures = 0
            if not device.online:
                device.online = True
        await self.manager.set_scheduled_updates(self.device_name, enabled=enabled)

    def is_online(self) -> bool:
        """Return True if the device currently has an active transport connection."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return False
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return device.online
        if handle.has_transport(TransportType.BLE):
            if handle.is_transport_connected(TransportType.BLE):
                return True
        return not handle.availability.mqtt_reported_offline

    async def async_refresh_login(self, exc: Exception | None = None) -> None:
        """Refresh login credentials asynchronously.

        LoginFailedError means the client already exhausted all recovery options
        (targeted refresh → force refresh → full re-login).  Raise ConfigEntryAuthFailed
        so HA tells the user to reconfigure the integration.

        For other auth errors, selectively refresh the affected transport:
        - SessionExpiredError: refreshes credentials for the specific transport.
        - AuthError (generic): performs a full login refresh.
        - Other/unknown: performs a full login refresh.
        """

        if isinstance(exc, LoginFailedError):
            raise ConfigEntryAuthFailed(
                f"Login failed for Mammotion account: {exc.reason}"
            ) from exc
        try:
            if (
                isinstance(exc, SessionExpiredError)
                and self.manager.token_manager is not None
            ):
                await self.manager.token_manager.refresh_aliyun_credentials()
            elif isinstance(exc, AuthError) and self.manager.token_manager is not None:
                await self.manager.token_manager.refresh_mqtt_credentials()
            else:
                await self.manager.refresh_login(self.account)
                self.store_cloud_credentials()
        except ReLoginRequiredError:
            try:
                await self.manager.refresh_login(self.account)
                self.store_cloud_credentials()
            except LoginFailedError as err:
                raise ConfigEntryAuthFailed(
                    f"Login failed for Mammotion account: {err.reason}"
                ) from err

    async def async_send_and_wait(
        self,
        command: str,
        expected_field: str,
        **kwargs: Any,
    ) -> None:
        """Send a command and wait for response with standard exception handling.

        Handles credential expiry, gateway/transport timeouts, and device-offline
        conditions uniformly.  Re-raises DeviceOfflineException after marking the
        device offline so callers can bail out of their update loops.
        """
        device = self.manager.get_device_by_name(self.device_name)
        if device is None or not self.is_online():
            return

        try:
            await self.manager.send_command_and_wait(
                self.device_name, command, expected_field, **kwargs
            )
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
        except DeviceOfflineException:
            device = self.manager.get_device_by_name(self.device_name)
            if device is not None:
                self.device_offline(device)
        except TooManyRequestsException as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="api_limit_exceeded"
            ) from exc
        except (
            GatewayTimeoutException,
            CommandTimeoutError,
            ConcurrentRequestError,
            NoTransportAvailableError,
        ):
            pass

    @staticmethod
    def device_offline(device: MowingDevice | RTKBaseStationDevice) -> None:
        """Mark the device as offline in its state model."""
        device.online = False

    def store_cloud_credentials(self) -> None:
        """Store cloud credentials in config entry."""
        if config_entry := self.config_entry:
            cache = self.manager.to_cache()
            if not cache:
                return
            # Translate library key "connect_response" → HA key CONF_CONNECT_DATA
            translated = {
                (CONF_CONNECT_DATA if k == "connect_response" else k): v
                for k, v in cache.items()
            }
            self.hass.config_entries.async_update_entry(
                config_entry, data={**config_entry.data, **translated}
            )

    async def async_send_command(self, command: str, **kwargs: Any) -> bool | None:
        """Send command via MammotionClient command queue."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None or not self.is_online():
            return False

        try:
            await self.manager.send_command_with_args(
                self.device_name, command, **kwargs
            )
            self.update_failures = 0
            return True
        except FailedRequestException:
            self.update_failures += 1
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
        except GatewayTimeoutException as ex:
            LOGGER.error(f"Gateway timeout exception: {ex.iot_id}")
            self.update_failures = 0
            return False
        except (DeviceOfflineException, NoConnectionException):
            self.device_offline(device)
            # Fall back to BLE if the cloud path is unavailable
            try:
                await self.manager.send_command_with_args(
                    self.device_name, command, prefer_ble=True, **kwargs
                )
                return True
            except COMMAND_EXCEPTIONS as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="command_failed"
                ) from exc
        except NoTransportAvailableError:
            LOGGER.debug(
                "No transport connected yet for %s, command '%s' skipped",
                self.device_name,
                command,
            )
            return False
        return False

    async def async_send_cloud_command(
        self, iot_id: str, command: bytes
    ) -> bool | None:
        """Send a raw cloud command via the device's active transport."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None or not self.is_online():
            return False
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return False

        try:
            await handle.send_raw(command)
            self.update_failures = 0
            return True
        except FailedRequestException:
            self.update_failures += 1
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
        except GatewayTimeoutException as ex:
            LOGGER.error(f"Gateway timeout exception: {ex.iot_id}")
            self.update_failures = 0
            return False
        except (DeviceOfflineException, NoConnectionException) as ex:
            LOGGER.error(f"Device offline: {ex.iot_id}")
            self.device_offline(device)
            return False
        except TooManyRequestsException as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="api_limit_exceeded"
            ) from exc
        return False

    async def async_send_bluetooth_command(
        self, key: str, **kwargs: Any
    ) -> bool | None:
        """Send command via BLE transport."""
        await self.async_send_command(key, prefer_ble=True, **kwargs)

    async def check_firmware_version(self) -> None:
        """Check if firmware version is updated."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return
        device_registry = dr.async_get(self.hass)
        device_entry = device_registry.async_get_device(
            identifiers={(DOMAIN, self.device_name)}
        )
        if device_entry is None:
            return

        new_swversion = device.device_firmwares.device_version

        if new_swversion is not None and new_swversion != device_entry.sw_version:
            device_registry.async_update_device(
                device_entry.id, sw_version=new_swversion
            )

        if model_id := device.mower_state.model_id:
            if model_id is not None and model_id != device_entry.model_id:
                device_registry.async_update_device(device_entry.id, model_id=model_id)

    async def update_firmware(self, version: str) -> None:
        """Update firmware and clear cached version info so it is re-fetched after the upgrade."""
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return
        device = self.manager.get_device_by_name(self.device_name)
        if device is not None:
            device.clear_version_info()
        http = self.manager.mammotion_http
        if http is not None:
            await http.start_ota_upgrade(handle.iot_id, version)

    async def async_sync_maps(self) -> None:
        """Get map data from the device."""
        try:
            await self.manager.start_map_sync(self.device_name)
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
            if self.update_failures < 5:
                await self.async_sync_maps()

    async def async_sync_schedule(self) -> None:
        """Sync scheduled mowing plans from the device."""
        try:
            await self.async_send_command("read_plan", sub_cmd=2, plan_index=0)
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
            if self.update_failures < 5:
                await self.async_sync_schedule()

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

    async def async_set_non_work_hours(self, start_time: str, end_time: str) -> None:
        """Set non work hours.

        start_time and end_time are in HH:MM format (24-hour).
        The proto field expects minutes-from-midnight as a string (e.g. "1320" for 22:00).
        """
        if start_time == end_time:
            await self.async_send_command("job_do_not_disturb_del")
            return

        def _to_minutes(hhmm: str) -> str:
            h, m = hhmm.split(":")
            return str(int(h) * 60 + int(m))

        await self.async_send_command(
            "job_do_not_disturb",
            unable_end_time=_to_minutes(end_time),
            unable_start_time=_to_minutes(start_time),
        )

    async def async_reset_blade_time(self) -> None:
        """Reset blade used time."""
        await self.async_send_command("reset_blade_time")

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

    async def async_reset_blade_warning_time(self) -> None:
        """Reset blade used time to zero."""
        await self.async_send_command("reset_blade_time")

    async def async_set_blade_warning_time(self, hours: int) -> None:
        """Set the blade warning time in hours."""
        await self.async_send_command("set_blade_warning_time", hours=hours)

    async def async_set_speed(self, speed: float) -> None:
        """Set working speed."""
        await self.async_send_command("set_speed", speed=speed)

    async def async_leave_dock(self) -> None:
        """Leave dock."""
        await self.send_command_and_update("leave_dock")

    async def async_cancel_task(self) -> None:
        """Cancel task."""
        await self.send_command_and_update("cancel_job")

    async def _async_ensure_ble_client(self) -> None:
        """Attach a BLE transport if we have an address but no client yet.

        Called before movement commands that prefer BLE so that a freshly
        discovered device (or one that was out of range at startup) gets a
        transport without waiting for the next full coordinator refresh.
        """
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return
        ble_mac = device.mower_state.ble_mac
        if not ble_mac:
            return
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return
        if handle.has_transport(TransportType.BLE):
            await handle.connect_transport(TransportType.BLE)
            return
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, ble_mac.upper(), True
        )
        if ble_device is None:
            return
        await self.manager.add_ble_to_device(self.device_name, ble_device)

    async def async_move_forward(self, speed: float, use_wifi: bool = False) -> None:
        """Move forward. Prefer BLE unless use_wifi=True (lower latency for manual control)."""
        if not use_wifi:
            await self._async_ensure_ble_client()
        await self.async_send_command(
            "move_forward", prefer_ble=not use_wifi, linear=speed
        )

    async def async_move_left(self, speed: float, use_wifi: bool = False) -> None:
        """Move left. Prefer BLE unless use_wifi=True."""
        if not use_wifi:
            await self._async_ensure_ble_client()
        await self.async_send_command(
            "move_left", prefer_ble=not use_wifi, angular=speed
        )

    async def async_move_right(self, speed: float, use_wifi: bool = False) -> None:
        """Move right. Prefer BLE unless use_wifi=True."""
        if not use_wifi:
            await self._async_ensure_ble_client()
        await self.async_send_command(
            "move_right", prefer_ble=not use_wifi, angular=speed
        )

    async def async_move_back(self, speed: float, use_wifi: bool = False) -> None:
        """Move back. Prefer BLE unless use_wifi=True."""
        if not use_wifi:
            await self._async_ensure_ble_client()
        await self.async_send_command(
            "move_back", prefer_ble=not use_wifi, linear=speed
        )

    async def async_rtk_dock_location(self) -> None:
        """RTK and dock location."""
        await self.async_send_and_wait(
            "read_write_device",
            "bidire_comm_cmd",
            rw_id=5,
            rw=1,
            context=1,
        )

    async def async_get_area_list(self) -> None:
        """Mowing area List."""
        await self.async_send_command(
            "get_area_name_list", device_id=self.device.iot_id
        )

    async def async_relocate_charging_station(self) -> None:
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
        await self.async_request_iot_sync_continuous()

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
                RptInfoType.RIT_CONNECT,
                RptInfoType.RIT_FW_INFO,
                RptInfoType.RIT_VISION_POINT,
                RptInfoType.RIT_VISION_STATISTIC,
                RptInfoType.RIT_CUTTER_INFO,
                RptInfoType.RIT_RTK,
            ],
            timeout=10000,
            period=3000,
            no_change_period=4000,
            count=1,
        )

    async def async_request_iot_sync_continuous(
        self, stop: bool = False, period=1000, no_change_period=4000
    ) -> None:
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
            period=period,
            no_change_period=no_change_period,
            count=0,
        )

    async def async_request_iot_sync_continuous_stop(self) -> None:
        """Stop sync specific info from device."""
        await self.async_send_command(
            "request_iot_sys",
            rpt_act=RptAct.RPT_STOP,
            rpt_info_type=[
                RptInfoType.RIT_DEV_STA,
                RptInfoType.RIT_DEV_LOCAL,
                RptInfoType.RIT_WORK,
                RptInfoType.RIT_MAINTAIN,
                RptInfoType.RIT_BASESTATION_INFO,
                RptInfoType.RIT_VIO,
            ],
            count=1,
        )

    async def send_svg_command(self, command_str: str, **kwargs: Any) -> None:
        """Send command and update."""
        svg_message = SvgMessage()

        return await self.async_send_command("send_svg_data", svg_message=svg_message)

    def generate_route_information(
        self, operation_settings: OperationSettings
    ) -> GenerateRouteInformation:
        """Generate route information."""
        device: MowingDevice = self.data
        if device.report_data.dev:
            dev = device.report_data.dev
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
        return route_information

    async def async_plan_route(
        self, operation_settings: OperationSettings
    ) -> bool | None:
        """Plan mow."""
        route_information = self.generate_route_information(operation_settings)

        # not sure if this is artificial limit
        # if (
        #     DeviceType.is_mini_or_x_series(device_name)
        #     and route_information.toward_mode == 0
        # ):
        #     route_information.toward = 0
        await self.async_send_and_wait(
            "generate_route_information",
            "bidire_reqconver_path",
            generate_route_information=route_information,
        )
        return True

    async def async_get_plan_route(self, operation_settings: OperationSettings) -> None:
        """Fetch the previously generated mow path from the device without replanning."""
        route_information = self.generate_route_information(operation_settings)
        await self.manager.start_mow_path_saga(
            self.device_name,
            zone_hashs=list(operation_settings.areas),
            route_info=route_information,
            skip_planning=True,
        )

    async def async_modify_plan_route(
        self, operation_settings: OperationSettings
    ) -> bool | None:
        """Modify plan mow."""

        if work := self.data.work:
            operation_settings.areas = list(dict.fromkeys(work.zone_hashs))
            operation_settings.toward = work.toward
            operation_settings.toward_mode = work.toward_mode
            operation_settings.toward_included_angle = work.toward_included_angle
            operation_settings.mowing_laps = work.edge_mode
            operation_settings.job_mode = work.job_mode
            operation_settings.job_id = work.job_id
            operation_settings.job_version = work.job_ver

        route_information = self.generate_route_information(operation_settings)

        return await self.async_send_command(
            "modify_route_information", generate_route_information=route_information
        )

    async def start_task(self, plan_id: str) -> None:
        """Start task."""
        await self.async_send_and_wait(
            "single_schedule", "todev_planjob_set", plan_id=plan_id
        )
        await self.async_request_iot_sync_continuous()

    async def async_restart_mower(self) -> None:
        """Restart mower."""
        await self.async_send_command("remote_restart")

    def clear_update_failures(self) -> None:
        """Clear update failures and reconnect transports if needed."""
        self.update_failures = 0

    @property
    def operation_settings(self) -> OperationSettings:
        """Return operation settings for planning."""
        return self._operation_settings

    async def async_modify_plan_if_mowing(self) -> None:
        """Re-plan the current mow route if the device is actively mowing."""
        if (
            int(self.data.report_data.work.bp_hash) in self.data.work.zone_hashs
            and (self.data.report_data.work.area >> 16) != 100
        ):
            await self.async_modify_plan_route(self.operation_settings)

    async def async_restore_data(self) -> None:
        """Restore saved data."""
        store = MammotionConfigStore(
            self.hass, version=1, minor_version=2, key=self.device_name
        )
        restored_data: Mapping[str, Any] | None = await store.async_load()

        handle = self.manager.mower(self.device_name)

        if restored_data is None:
            empty = MowingDevice()
            self.data = empty
            if handle is not None:
                handle.restore_device(empty)
            return

        try:
            if restored_data is not None:
                mower_state = MowingDevice().from_dict(restored_data)
                if handle is not None:
                    handle.restore_device(mower_state)
                    self.data = mower_state
        except InvalidFieldValue:
            empty = MowingDevice()
            self.data = empty
            if handle is not None:
                handle.restore_device(empty)

    async def async_save_data(self, data: MowingDevice) -> None:
        """Get map data from the device."""
        store = Store(self.hass, version=1, minor_version=2, key=self.device_name)
        await store.async_save(data.to_dict())

    async def _async_update_data(self) -> DataT | None:
        """Update data from the device."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return None

        if not device.enabled:
            return self.get_coordinator_data(device)

        handle = self.manager.mower(self.device_name)

        if not self.is_online():
            return self.get_coordinator_data(device)

        # Update BLE device address from HA bluetooth scanner if available
        if (
            device.mower_state.ble_mac != ""
            and handle is not None
            and handle.prefer_ble
        ):
            if ble_device := bluetooth.async_ble_device_from_address(
                self.hass, device.mower_state.ble_mac.upper(), True
            ):
                await self.manager.update_ble_device(self.device_name, ble_device)

        # Don't query the mower while users are doing map changes or it's updating.
        if device.report_data.dev.sys_status in NO_REQUEST_MODES:
            return self.get_coordinator_data(device)

        if self.update_failures > 5:
            async_call_later(
                self.hass,
                60,
                HassJob(lambda _: self.clear_update_failures()),
            )
            return self.get_coordinator_data(device)

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
        handle = self.manager.mower(self.device_name)
        if handle is not None:
            self._subscriptions.extend(
                [
                    handle.subscribe_state_changed(
                        self._guarded(self._on_state_changed)
                    ),
                    handle.subscribe_device_status(
                        self._guarded(self._async_update_status)
                    ),
                    handle.subscribe_device_properties(
                        self._guarded(self._async_update_properties)
                    ),
                    handle.subscribe_device_event(
                        self._guarded(self._async_update_event_message)
                    ),
                ]
            )

    def _guarded(self, method: Any) -> Any:
        """Wrap a callback so it silently skips when HA is shutting down.

        During shutdown aiohttp's websocket layer may already be closing.
        Pushing state updates at that point raises ClientConnectionResetError
        inside shielded futures and logs noisy tracebacks.  Checking
        hass.is_stopping before every push prevents the error entirely.
        """

        async def _wrapper(*args: Any, **kwargs: Any) -> None:
            if self.hass.is_stopping:
                return
            await method(*args, **kwargs)

        return _wrapper

    async def async_shutdown(self) -> None:
        """Cancel all RAII subscriptions and delegate to HA coordinator shutdown."""
        for sub in self._subscriptions:
            sub.cancel()
        self._subscriptions.clear()
        await super().async_shutdown()

    async def _on_state_changed(self, snapshot: DeviceSnapshot) -> None:
        """Push updated device data to HA."""
        self.async_set_updated_data(snapshot.raw)

    def find_entity_by_attribute_in_registry(
        self, attribute_name: str, attribute_value: Any
    ) -> tuple[str | None, er.RegistryEntry | None]:
        """Find an entity using the entity registry based on attributes."""
        entity_registry = er.async_get(self.hass)

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
            if area_frame is not None:
                area_name: AreaHashNameList = next(
                    (
                        area
                        for area in self.data.map.area_name
                        if area.hash == area_frame.hash
                    ),
                    None,
                )
                name = area_name.name if area_name is not None else None
        else:
            return "path"

        return name if name else f"area {area_hash}"


class MammotionReportUpdateCoordinator(MammotionBaseUpdateCoordinator[MowingDevice]):
    """Mammotion report update coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: MammotionClient,
        unique_name: str | None = None,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=REPORT_INTERVAL,
            unique_name=unique_name,
        )

    def get_coordinator_data(self, device: MowingDevice) -> MowingDevice:
        """Get coordinator data."""
        return device

    async def _async_update_data(self) -> MowingDevice:
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data

        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            LOGGER.debug("device not found")
            return self.data

        LOGGER.debug("Updated Mammotion device %s", self.device_name)
        self.update_failures = 0
        if data := self.manager.get_device_by_name(self.device_name):
            await self.async_save_data(data)
            return data
        return self.data

    async def _async_update_notification(self, res: tuple[str, Any | None]) -> None:
        """Update data from incoming messages."""
        if device := self.manager.get_device_by_name(self.device_name):
            self.async_set_updated_data(device)

    async def _async_update_properties(
        self, properties: ThingPropertiesMessage
    ) -> None:
        """Update data from incoming properties messages."""
        if not self.data.enabled:
            return
        if not self.is_online():
            await self.set_scheduled_updates(True)
        if device := self.manager.get_device_by_name(self.device_name):
            self.async_set_updated_data(device)

    async def _async_update_status(self, status: ThingStatusMessage) -> None:
        """Update data from incoming status messages."""
        if not self.data.enabled:
            return
        if status.params.status.value == StatusType.CONNECTED:
            await self.set_scheduled_updates(True)
            self.hass.async_create_task(self.async_request_refresh())
        if device := self.manager.get_device_by_name(self.device_name):
            self.async_set_updated_data(device)

    async def _async_update_event_message(self, event: ThingEventMessage) -> None:
        """Update data from incoming event messages."""
        if not self.data.enabled:
            return
        if not self.is_online():
            await self.set_scheduled_updates(True)
        if device := self.manager.get_device_by_name(self.device_name):
            self.async_set_updated_data(device)

    async def _async_setup(self):
        await super()._async_setup()
        await self.async_request_iot_sync()


class MammotionMaintenanceUpdateCoordinator(MammotionBaseUpdateCoordinator[Maintain]):
    """Class to manage fetching mammotion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: MammotionClient,
        unique_name: str | None = None,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=MAINTENANCE_INTERVAL,
            unique_name=unique_name,
        )

        mowing_device = self.manager.get_device_by_name(self.device_name)
        if self.data is None:
            self.data = mowing_device.report_data.maintenance

    def get_coordinator_data(self, device: MowingDevice) -> Maintain:
        """Get coordinator data."""
        return device.report_data.maintenance

    async def _on_state_changed(self, snapshot: DeviceSnapshot) -> None:
        data = cast(MowerDevice, snapshot.raw)
        self.async_set_updated_data(data.report_data.maintenance)

    async def _async_update_data(self) -> Maintain:
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data

        try:
            await self.async_send_command("get_maintenance")

            await self.async_send_and_wait(
                "read_job_do_not_disturb", "todev_unable_time_set"
            )

        except DeviceOfflineException as ex:
            if ex.iot_id == self.device.iot_id:
                device = self.manager.get_device_by_name(self.device_name)
                self.device_offline(device)
                return device.report_data.maintenance
        except GatewayTimeoutException:
            pass

        return self.manager.get_device_by_name(
            self.device.device_name
        ).report_data.maintenance

    async def _async_setup(self) -> None:
        """Setup maintenance coordinator."""
        await super()._async_setup()


class MammotionDeviceVersionUpdateCoordinator(
    MammotionBaseUpdateCoordinator[MowingDevice]
):
    """Class to manage fetching mammotion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: MammotionClient,
        unique_name: str | None = None,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=DEFAULT_INTERVAL,
            unique_name=unique_name,
        )

        mowing_device = self.manager.get_device_by_name(self.device_name)
        if self.data is None:
            self.data = mowing_device

    def get_coordinator_data(self, device: MowingDevice) -> MowingDevice:
        """Get coordinator data."""
        return device

    async def _async_update_data(self) -> MowingDevice:
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data
        device = self.manager.get_device_by_name(self.device_name)
        handle = self.manager.mower(self.device_name)

        checks: list[tuple[str, str, bool]] = [
            (
                "get_device_version_main",
                "toapp_devinfo_resp",
                bool(device.mower_state.swversion),
            ),
            (
                "get_device_version_info",
                "toapp_dev_fw_info",
                bool(device.device_firmwares.main_controller),
            ),
            (
                "get_device_base_info",
                "toapp_devinfo_resp",
                bool(device.device_firmwares.device_version),
            ),
            (
                "get_device_product_model",
                "device_product_type_info",
                bool(device.mower_state.model_id),
            ),
        ]
        for command, expected_field, already_set in checks:
            if already_set:
                continue
            try:
                await self.async_send_and_wait(command, expected_field)
            except DeviceOfflineException:
                return device

        await self.check_firmware_version()

        if handle is not None:
            http = self.manager.mammotion_http
            if http is not None:
                ota_info = await http.get_device_ota_firmware([handle.iot_id])
                LOGGER.debug("OTA info: %s", ota_info.data)
                if check_versions := ota_info.data:
                    for check_version in check_versions:
                        if check_version.device_id == handle.iot_id:
                            device.update_check = check_version

        if device.mower_state.model_id != "":
            self.update_interval = DEVICE_VERSION_INTERVAL

        return device

    async def _async_setup(self) -> None:
        """Setup device version coordinator."""
        await super()._async_setup()

        try:
            device = self.manager.get_device_by_name(self.device_name)
            if device is None:
                return

            checks: list[tuple[str, str, bool]] = [
                (
                    "get_device_version_main",
                    "toapp_devinfo_resp",
                    bool(device.mower_state.swversion),
                ),
                (
                    "get_device_version_info",
                    "toapp_dev_fw_info",
                    bool(device.device_firmwares.main_controller),
                ),
                (
                    "get_device_base_info",
                    "toapp_devinfo_resp",
                    bool(device.device_firmwares.device_version),
                ),
                (
                    "get_device_product_model",
                    "device_product_type_info",
                    bool(device.mower_state.model_id),
                ),
            ]
            for command, expected_field, already_set in checks:
                if already_set:
                    continue
                try:
                    await self.async_send_and_wait(command, expected_field)
                except DeviceOfflineException:
                    pass

            if not device.mower_state.wifi_mac:
                await self.async_send_command("get_device_network_info")

            handle = self.manager.mower(self.device_name)
            if handle is not None:
                http = self.manager.mammotion_http
                if http is not None:
                    ota_info = await http.get_device_ota_firmware([handle.iot_id])
                    device = self.manager.get_device_by_name(self.device_name)
                    if check_versions := ota_info.data:
                        for check_version in check_versions:
                            if check_version.device_id == handle.iot_id:
                                device.update_check = check_version

            self.async_set_updated_data(self.data)
        except DeviceOfflineException:
            pass


class MammotionMapUpdateCoordinator(MammotionBaseUpdateCoordinator[MowerInfo]):
    """Class to manage fetching mammotion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: MammotionClient,
        unique_name: str | None = None,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=MAP_INTERVAL,
            unique_name=unique_name,
        )

        mowing_device = self.manager.get_device_by_name(self.device_name)
        if self.data is None:
            self.data = mowing_device.mower_state

    def get_coordinator_data(self, device: MowingDevice) -> MowerInfo:
        """Get coordinator data."""
        return device.mower_state

    def _map_callback(self) -> None:
        """Trigger a resync when the bol hash changes."""
        # TODO setup callback to get bol hash data

    async def _async_update_data(self) -> MowerInfo:
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data
        device = self.manager.get_device_by_name(self.device_name)

        try:
            if (
                round(device.location.RTK.latitude, 0) == 0
                or round(device.location.dock.latitude, 0) == 0
            ):
                await self.async_rtk_dock_location()

            if len(device.map.hashlist) == 0 or len(device.map.missing_hashlist()) > 0:
                await self.manager.start_map_sync(self.device_name)

        except DeviceOfflineException as ex:
            if ex.iot_id == self.device.iot_id:
                self.device_offline(device)
                return device.mower_state
        except GatewayTimeoutException:
            pass
        except (ConcurrentRequestError, NoTransportAvailableError):
            pass

        return self.manager.get_device_by_name(self.device_name).mower_state

    async def _async_setup(self) -> None:
        """Setup coordinator with initial call to get map data."""
        await super()._async_setup()
        device = self.manager.get_device_by_name(self.device_name)

        if not device.enabled or not device.online:
            return
        try:
            await self.async_rtk_dock_location()
            if not DeviceType.is_luba1(self.device_name):
                await self.async_get_area_list()
        except DeviceOfflineException as ex:
            if ex.iot_id == self.device.iot_id:
                self.device_offline(device)
        except GatewayTimeoutException:
            pass
        except NoTransportAvailableError:
            LOGGER.debug(
                "No transport connected yet for %s, map data will be fetched on next update",
                self.device_name,
            )


class MammotionDeviceErrorUpdateCoordinator(
    MammotionBaseUpdateCoordinator[MowingDevice]
):
    """Class to manage fetching mammotion data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: MammotionClient,
        unique_name: str | None = None,
    ) -> None:
        """Initialize global mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=DEFAULT_INTERVAL,
            unique_name=unique_name,
        )
        mowing_device = self.manager.get_device_by_name(self.device_name)
        if self.data is None:
            self.data = mowing_device

    def get_coordinator_data(self, device: MowingDevice) -> MowingDevice:
        """Get coordinator data."""
        return device

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
                if device := self.manager.get_device_by_name(self.device_name):
                    self.async_set_updated_data(device)
            except json.JSONDecodeError:
                """Failed to parse warning event."""

    async def _async_update_notification(self, res: tuple[str, Any | None]) -> None:
        """Update data from incoming notifications messages."""
        if res[0] == "sys" and res[1] is not None:
            sys_msg = betterproto2.which_one_of(res[1], "SubSysMsg")
            if sys_msg[0] == "system_update_buf" and sys_msg[1] is not None:
                buffer_list: SystemUpdateBufMsg = sys_msg[1]
                if buffer_list.update_buf_data[0] == 2:
                    if device := self.manager.get_device_by_name(self.device_name):
                        self.async_set_updated_data(device)

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

    async def _async_update_data(self) -> MowingDevice:
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data
        device = self.manager.get_device_by_name(self.device_name)
        try:
            if not device.errors.error_codes:
                http = self.manager.mammotion_http
                if http is not None:
                    device.errors.error_codes = await http.get_all_error_codes()
        except DeviceOfflineException:
            return device

        return device

    async def _on_sys_status_changed(self, sys_status: WorkMode) -> None:
        """Handle sys status changed."""
        if sys_status in (
            WorkMode.MODE_WORKING,
            WorkMode.MODE_RETURNING,
            WorkMode.MODE_LOCK,
            WorkMode.MODE_PAUSE,
        ):
            await self.async_send_and_wait(
                "read_write_device", "bidire_comm_cmd", rw_id=5, rw=1, context=2
            )
            await self.async_send_and_wait(
                "read_write_device", "bidire_comm_cmd", rw_id=5, rw=1, context=3
            )

    async def _async_setup(self) -> None:
        """Set up the device-version coordinator."""
        await super()._async_setup()
        device = self.manager.get_device_by_name(self.device_name)
        if handle := self.manager.mower(self.device_name):
            handle.watch_field(
                lambda s: s.raw.report_data.dev.sys_status,
                self._on_sys_status_changed,
            )

        try:
            await self.async_send_and_wait(
                "read_write_device", "bidire_comm_cmd", rw_id=5, rw=1, context=2
            )
            await self.async_send_and_wait(
                "read_write_device", "bidire_comm_cmd", rw_id=5, rw=1, context=3
            )
            if not device.errors.error_codes:
                http = self.manager.mammotion_http
                if http is not None:
                    device.errors.error_codes = await http.get_all_error_codes()

            self.async_set_updated_data(self.data)
        except DeviceOfflineException:
            pass


class MammotionRTKCoordinator(MammotionBaseUpdateCoordinator[RTKBaseStationDevice]):
    """Mammotion DataUpdateCoordinator for RTK base station devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: MammotionClient,
        unique_name: str | None = None,
    ) -> None:
        """Initialize rtk mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=RTK_INTERVAL,
            unique_name=unique_name,
        )

    async def get_coordinator_data(
        self, device: RTKBaseStationDevice
    ) -> RTKBaseStationDevice:
        """Return the current RTK device state tracked by this coordinator."""
        return self.data

    async def async_restore_data(self) -> None:
        """Restore saved data."""
        store = MammotionConfigStore(
            self.hass, version=1, minor_version=2, key=self.device_name
        )
        restored_data: Mapping[str, Any] | None = await store.async_load()

        handle = self.manager.rtk_device(self.device_name)

        if restored_data is None:
            empty = RTKBaseStationDevice()
            self.data = empty
            if handle is not None:
                handle.restore_device(empty)
            return

        try:
            if restored_data is not None:
                rtk_state = RTKBaseStationDevice().from_dict(restored_data)
                if handle is not None:
                    handle.restore_device(rtk_state)
                    self.data = rtk_state
        except InvalidFieldValue:
            empty = RTKBaseStationDevice()
            self.data = empty
            if handle is not None:
                handle.restore_device(empty)

    async def _async_update_data(self) -> RTKBaseStationDevice:
        """Return current RTK state from the device handle's state machine.

        The state machine is kept up to date automatically by:
        - LubaMsg protobuf frames → RTKStateReducer.apply()
        - thing/properties JSON pushes → RTKStateReducer.apply_properties()
        - thing/status pushes → DeviceHandle.on_status_message()

        The only remaining polling work is the OTA firmware check, which is
        not pushed via MQTT and must be fetched from the HTTP API.
        """
        handle = self.manager.rtk_device(self.device_name)
        if handle is None:
            return self.data

        await self.async_send_command("send_todev_ble_sync", sync_type=3)
        await self.async_send_and_wait("basestation_info", "to_app")

        http = self.manager.mammotion_http
        if http is not None:
            try:
                ota_info = await http.get_device_ota_firmware([self.device.iot_id])
                if check_versions := ota_info.data:
                    for check_version in check_versions:
                        if check_version.device_id == self.device.iot_id:
                            self.data.update_check = check_version
            except ReLoginRequiredError:
                await self.async_refresh_login()
            except (DeviceOfflineException, GatewayTimeoutException):
                pass

        return self.data

    async def async_shutdown(self) -> None:
        """Cancel all RAII subscriptions and delegate to HA coordinator shutdown."""
        for sub in self._subscriptions:
            sub.cancel()
        self._subscriptions.clear()
        await super().async_shutdown()

    async def _async_update_notification(self, res: tuple[str, Any | None]) -> None:
        """Handle update notifications for the RTK device."""
        if rtk_device := self.manager.rtk_device(self.device_name):
            self.async_set_updated_data(
                cast(RTKBaseStationDevice, rtk_device.snapshot.raw)
            )

    async def _async_setup(self) -> None:
        """Set up RTK device subscriptions and fetch one-time HTTP data."""
        await super()._async_setup()
        if handle := self.manager.rtk_device(self.device_name):
            updated = handle.snapshot.raw
            updated.product_key = self.device.product_key
            updated.iot_id = self.device.iot_id
            updated.name = self.device.device_name
            snapshot, _ = handle.state_machine.apply(updated, handle.availability)

        # Fetch lora version — only available via HTTP, not MQTT/protobuf.
        await self.manager.fetch_rtk_lora_info(self.device_name)

        if (gateway := self.manager.cloud_gateway) and DeviceType.is_aliyun_product_key(
            self.data.product_key
        ):
            await self.manager.fetch_rtk_properties(self.device_name)
            await gateway.get_device_status(self.device.iot_id)
            await self.async_send_command("send_todev_ble_sync", sync_type=3)
            await self.async_request_iot_sync()
            await self.async_send_and_wait("basestation_info", "to_app")
            await self.async_send_and_wait(
                "get_device_network_info", "toapp_networkinfo_rsp"
            )
        self.data.online = True

    async def update_firmware(self, version: str) -> None:
        """Update firmware."""
        http = self.manager.mammotion_http
        if http is not None:
            await http.start_ota_upgrade(self.device.iot_id, version)
