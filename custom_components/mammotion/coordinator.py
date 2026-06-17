"""Provides the mammotion DataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import json
import secrets
import time
from abc import abstractmethod
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from habluetooth import BluetoothScanningMode
from habluetooth.models import BluetoothServiceInfoBleak
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    async_register_callback,
)
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from mashumaro.exceptions import InvalidFieldValue
from pymammotion.aliyun.exceptions import (
    CloudSetupError,
    DeviceOfflineException,
    FailedRequestException,
    GatewayTimeoutException,
    TooManyRequestsException,
)
from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.client import MammotionClient
from pymammotion.data.model import GenerateRouteInformation
from pymammotion.data.model.device import (
    MowerDevice,
    MowerInfo,
    MowingDevice,
    PoolCleanerDevice,
    RTKBaseStationDevice,
)
from pymammotion.data.model.device_config import OperationSettings, create_path_order
from pymammotion.data.model.hash_list import AreaHashNameList, Plan, SvgMessage
from pymammotion.data.model.pool_state import PoolPlan, SpinoToggle
from pymammotion.data.model.report_info import Maintain, NetUsedType
from pymammotion.data.mqtt.event import DeviceNotificationEventParams, ThingEventMessage
from pymammotion.data.mqtt.properties import ThingPropertiesMessage
from pymammotion.data.mqtt.status import StatusType, ThingStatusMessage
from pymammotion.http.model.camera_stream import (
    StreamSubscriptionResponse,
)
from pymammotion.http.model.http import ErrorInfo, Response
from pymammotion.mammotion.commands.mammotion_command import MammotionCommand
from pymammotion.proto import MulSex
from pymammotion.state.device_state import DeviceShutdownEvent, DeviceSnapshot
from pymammotion.transport.base import (
    AuthError,
    BLEUnavailableError,
    CommandTimeoutError,
    ConcurrentRequestError,
    LoginFailedError,
    NoTransportAvailableError,
    ReLoginRequiredError,
    SessionExpiredError,
    Subscription,
    TransportType,
)
from pymammotion.transport.ble import BLETransport
from pymammotion.utility.constant import MOWING_ACTIVE_MODES, WorkMode
from pymammotion.utility.device_type import DeviceType
from pymammotion.utility.plan_id import make_copy_name, new_mower_plan_id
from pymammotion.utility.svg import chunk_svg_messages
from webrtc_models import RTCIceServer

from .agora_api import SERVICE_IDS, AgoraAPIClient, AgoraResponse
from .config import MammotionConfigStore
from .const import (
    CONF_ACCOUNTNAME,
    CONF_CONNECT_DATA,
    CONF_HAS_CLOUD_ACCOUNT,
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
REPORT_INTERVAL = timedelta(minutes=5)
DEVICE_VERSION_INTERVAL = timedelta(weeks=1)
MAP_INTERVAL = timedelta(minutes=60)
RTK_INTERVAL = timedelta(hours=5)
SPINO_INTERVAL = timedelta(weeks=1)

# Possible states for ``MammotionReportUpdateCoordinator.map_sync_status`` and
# the ``map_sync_status`` diagnostic ENUM sensor that surfaces it.
MAP_SYNC_STATUSES = ("synced", "syncing", "out_of_sync")

# Cloud response code returned by the stream-subscription endpoint when the
# device is unreachable ("Device not responding. Please check the network
# connection").  Treated as a device-offline signal.
DEVICE_NOT_RESPONDING_CODE = 50504


class MammotionBaseUpdateCoordinator[DataT](DataUpdateCoordinator[DataT]):  # type: ignore[misc]
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
        self._ice_servers = None
        self._agora_response = None
        self.service_info: BluetoothServiceInfoBleak | None = None
        assert config_entry.unique_id
        self.account = config_entry.data.get(CONF_ACCOUNTNAME, "")
        self.password = config_entry.data.get(CONF_PASSWORD, "")
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
        self._stream_data_fetched_at: float = 0.0  # monotonic timestamp of last fetch
        self._STREAM_TOKEN_TTL: float = 300.0  # seconds before we re-fetch
        _mammotion_data = config_entry.data.get(CONF_MAMMOTION_DATA) or {}
        try:
            _user_account = int(
                _mammotion_data["data"]["userInformation"]["userAccount"]
            )
        except (KeyError, TypeError, ValueError):
            _user_account = 0
        self.commands = MammotionCommand(device.device_name, _user_account)
        self._subscriptions: list[Subscription] = []
        self.map_offset_lat: float = 0.0
        self.map_offset_lon: float = 0.0
        self._bluetooth_enabled: bool = True
        self._cloud_enabled: bool = True

        mower_device = self.manager.get_device_by_name(self.device_name)

        if self.data is None:
            self.data = mower_device

    @property
    def has_cloud_account(self) -> bool:
        """Return True if cloud login is active for this entry."""
        if CONF_HAS_CLOUD_ACCOUNT in self.config_entry.data:
            return bool(self.config_entry.data[CONF_HAS_CLOUD_ACCOUNT])
        return bool(self.account)

    @abstractmethod
    def get_coordinator_data(self, device: MowingDevice) -> DataT:
        """Get coordinator data."""

    async def async_check_stream_expiry(
        self, force: bool = False
    ) -> tuple[StreamSubscriptionResponse | None, AgoraResponse | None]:
        """Return cached Agora stream data, refreshing only when the token is absent or stale."""
        now = time.monotonic()
        token_age = now - self._stream_data_fetched_at
        cached_data = self._stream_data

        if not force and (
            cached_data is not None
            and cached_data.data is not None
            and token_age < self._STREAM_TOKEN_TTL
            and self._agora_response is not None
        ):
            LOGGER.debug("Reusing cached stream token (age=%.0fs)", token_age)
            return cached_data.data, self._agora_response

        stream_data = None

        try:
            stream_data = await self.manager.get_stream_subscription(
                self.device_name, self.device.iot_id
            )
            self.set_stream_data(stream_data)
            self._stream_data_fetched_at = time.monotonic()

            # A 50504 means the cloud couldn't reach the device — bail out
            # cleanly rather than continuing on to the Agora setup with no data.
            if (
                stream_data is not None
                and stream_data.code == DEVICE_NOT_RESPONDING_CODE
            ):
                LOGGER.warning(
                    "Stream subscription for %s reports device not responding "
                    "(code %s: %s)",
                    self.device_name,
                    stream_data.code,
                    stream_data.msg,
                )
                return None, self._agora_response

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
        return (
            stream_data.data if stream_data is not None else None,
            self._agora_response,
        )

    def set_stream_data(
        self, stream_data: Response[StreamSubscriptionResponse]
    ) -> None:
        """Set stream data."""
        self._stream_data = stream_data

    def get_stream_data(self) -> Response[StreamSubscriptionResponse]:
        """Return stream data."""
        return self._stream_data

    @property
    def is_on_4g(self) -> bool:
        """Return True when the device's active network interface is 4G/cellular."""
        device = self.manager.get_device_by_name(self.device_name)
        try:
            return device.report_data.connect.used_net == NetUsedType.MNET
        except AttributeError:
            return False

    async def join_webrtc_channel(self) -> None:
        """Start stream command."""

    async def leave_webrtc_channel(self) -> None:
        """End stream command."""

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
        handle = self.manager.mower(self.device_name)
        if handle is not None:
            if enabled:
                await handle.restart_keep_alive()
            else:
                await handle.stop_polling()

    def is_online(self) -> bool:
        """Return True if the device currently has an active transport connection."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return False
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return bool(device.online)
        if handle.has_transport(TransportType.BLE) and (
            ble := handle.get_transport(TransportType.BLE)
        ):
            if ble.is_usable:
                return True
        return bool(not handle.availability.mqtt_reported_offline)

    @property
    def mqtt_transport_connected(self) -> bool:
        if handle := self.manager.mower(self.device_name):
            for t_type in (TransportType.CLOUD_ALIYUN, TransportType.CLOUD_MAMMOTION):
                if handle.is_transport_connected(t_type):
                    return True
        return False

    @property
    def mqtt_device_online(self) -> bool:
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return False
        if handle := self.manager.mower(self.device_name):
            return bool(not handle.availability.mqtt_reported_offline)
        return False

    @property
    def bluetooth_enabled(self) -> bool:
        """Return whether Bluetooth transport is enabled."""
        return self._bluetooth_enabled

    @property
    def cloud_enabled(self) -> bool:
        """Return whether Cloud transport is enabled."""
        return self._cloud_enabled

    async def async_set_bluetooth_enabled(self, enabled: bool) -> None:
        """Enable or disable Bluetooth transport."""
        self._bluetooth_enabled = enabled
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return
        if not enabled:
            handle.set_prefer_ble(value=False)
            await handle.disconnect_transport(TransportType.BLE)
        else:
            handle.set_prefer_ble(value=True)
            await self._async_ensure_ble_client()

    async def async_set_cloud_enabled(self, enabled: bool) -> None:
        """Enable or disable Cloud transport."""
        self._cloud_enabled = enabled
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return
        if enabled:
            for t_type in (TransportType.CLOUD_ALIYUN, TransportType.CLOUD_MAMMOTION):
                await handle.connect_transport(t_type)
            await handle.restart_keep_alive()
        else:
            for t_type in (TransportType.CLOUD_ALIYUN, TransportType.CLOUD_MAMMOTION):
                await handle.disconnect_transport(t_type)

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
        if not self.has_cloud_account:
            return

        if isinstance(exc, LoginFailedError):
            raise ConfigEntryAuthFailed(
                f"Login failed for Mammotion account: {exc}"
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
        except CloudSetupError as err:
            LOGGER.error("Aliyun cloud setup failed during re-login: %s", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cloud_setup_failed"
            ) from err
        except ReLoginRequiredError as err:
            raise ConfigEntryAuthFailed(
                f"Re-authentication required for Mammotion account: {err}"
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
                self.device_name,
                command,
                expected_field,
                prefer_ble=self._bluetooth_enabled,
                **kwargs,
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
        except NoTransportAvailableError as exc:
            LOGGER.debug(f"No Transport: {exc}")
        except (
            GatewayTimeoutException,
            CommandTimeoutError,
            ConcurrentRequestError,
        ):
            pass
        except asyncio.CancelledError:
            # bleak_retry_connector raises CancelledError when no BLE slot is
            # available (it cancels its own internal sleep).  Re-raise only when
            # the enclosing task is genuinely being cancelled; otherwise treat it
            # as a transient BLE failure and let setup continue.
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            LOGGER.debug(
                "BLE connection cancelled (no available slot) for %s — skipping",
                self.device_name,
            )

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
                self.device_name,
                command,
                prefer_ble=kwargs.pop("prefer_ble", self._bluetooth_enabled),
                skip_if_saga_active=False,
                **kwargs,
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
        except DeviceOfflineException:
            self.device_offline(device)
        except TooManyRequestsException as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="api_limit_exceeded"
            ) from exc
        except NoTransportAvailableError as exc:
            LOGGER.debug(
                "No transport connected yet for %s, command '%s' skipped",
                self.device_name,
                command,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_failed"
            ) from exc
            return False
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            LOGGER.debug(
                "BLE connection cancelled (no available slot) for %s — skipping",
                self.device_name,
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
        except (DeviceOfflineException, NoTransportAvailableError) as ex:
            LOGGER.error(f"Device offline: {ex.iot_id}")
            self.device_offline(device)
            return False
        except TooManyRequestsException as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="api_limit_exceeded"
            ) from exc
        except ReLoginRequiredError as err:
            raise ConfigEntryAuthFailed(
                f"Re-authentication required for Mammotion account: {err}"
            ) from err
        return False

    async def async_send_bluetooth_command(self, key: str, **kwargs: Any) -> None:
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
        """Sync all scheduled mowing plans from the device via PlanFetchSaga."""
        try:
            await self.manager.start_plan_sync(self.device_name)
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
            if self.update_failures < 5:
                await self.async_sync_schedule()

    async def async_fetch_audio_config(self) -> None:
        """Read current audio config (volume, language, gender) from device."""
        await self.async_send_and_wait("get_car_audio_cfg", "audio_cfg")

    async def async_set_voice_volume(self, volume: float) -> None:
        """Set robot voice volume (0–100)."""
        await self.async_send_and_wait(
            "set_car_volume", "set_audio", volume=int(volume)
        )

    async def async_set_voice_on_off(self, on: bool) -> None:
        """Turn robot voice on (restores 50%) or off (sets volume to 0)."""
        await self.async_send_and_wait(
            "set_car_volume", "set_audio", volume=50 if on else 0
        )

    async def async_set_voice_gender(self, sex: str) -> None:
        """Set robot voice gender (MAN or WOMAN)."""
        await self.async_send_and_wait(
            "set_car_volume_sex", "set_audio", sex=MulSex[sex]
        )

    async def async_start_stop_blades(
        self, start_stop: bool, blade_height: int = 60
    ) -> None:
        """Start stop blades."""
        if DeviceType.is_luba1(self.device_name):
            if start_stop:
                await self.async_send_and_wait(
                    "set_blade_control", "toapp_knife_status_change", on_off=1
                )
            else:
                await self.async_send_and_wait(
                    "set_blade_control", "toapp_knife_status_change", on_off=0
                )
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
        await self.async_send_and_wait(
            "reset_blade_time", "todev_reset_blade_used_time_status"
        )

    def _rw_expected_field(self, rw_id: int) -> str:
        """Return the expected response field for a read_write_device command.

        Mirrors the routing in MammotionCommand.read_write_device(): only
        rw_ids [3, 6, 7, 8, 10, 11] on Pro/X3 devices are sent via the nav
        adapter (nav_sys_param_cmd).  Every other rw_id — including 12 and 13
        used for wildlife safety — always goes through allpowerfull_rw() and
        responds on bidire_comm_cmd, regardless of device type.
        """
        if rw_id in (3, 6, 7, 8, 10, 11) and DeviceType.is_luba_pro(self.device_name):
            return "nav_sys_param_cmd"
        return "bidire_comm_cmd"

    async def async_set_rain_detection(self, on_off: bool) -> None:
        """Set rain detection."""
        await self.async_send_and_wait(
            "read_write_device",
            self._rw_expected_field(3),
            rw_id=3,
            context=int(on_off),
            rw=1,
        )

    async def async_read_rain_detection(self) -> None:
        """Read current rain detection state from device."""
        await self.async_send_and_wait(
            "read_write_device", self._rw_expected_field(3), rw_id=3, context=0, rw=0
        )

    async def async_set_sidelight(self, on_off: int) -> None:
        """Set Sidelight."""
        await self.async_send_and_wait(
            "read_and_set_sidelight",
            "todev_time_ctrl_light",
            is_sidelight=bool(on_off),
            operate=0,
        )

    async def async_read_sidelight(self) -> None:
        """Read current sidelight state from device."""
        await self.async_send_and_wait(
            "read_and_set_sidelight",
            "todev_time_ctrl_light",
            is_sidelight=False,
            operate=1,
        )

    async def async_set_manual_light(self, manual_ctrl: bool) -> None:
        """Set manual night light."""
        await self.async_send_and_wait(
            "set_car_manual_light", "set_lamp_rsp", manual_ctrl=manual_ctrl
        )

    async def async_read_manual_light(self) -> None:
        """Read current manual light state from device."""
        await self.async_send_and_wait("get_car_light", "get_lamp_rsp", ids=1126)

    async def async_set_night_light(self, night_light: bool) -> None:
        """Set night light."""
        await self.async_send_and_wait(
            "set_car_light", "set_lamp_rsp", on_off=night_light
        )

    async def async_read_night_light(self) -> None:
        """Read current night light state from device."""
        await self.async_send_and_wait("get_car_light", "get_lamp_rsp", ids=1123)

    async def async_set_traversal_mode(self, context: int) -> None:
        """Set traversal mode."""
        await self.async_send_and_wait(
            "traverse_mode", self._rw_expected_field(7), context=context
        )

    async def async_read_traversal_mode(self) -> None:
        """Read current traversal mode from device."""
        await self.async_send_and_wait(
            "read_write_device", self._rw_expected_field(7), rw_id=7, context=0, rw=0
        )

    async def async_set_wildlife_safety(self, mode: int) -> None:
        """Set wildlife safety mode (0=off, 1=stop mowing, 2=low-speed mowing).

        Sends rw_id=13 (status) first, then rw_id=12 (mode).  Both are sent
        via the device-appropriate channel (_rw_expected_field).
        """
        status = 0 if mode == 0 else 1
        await self.async_send_and_wait(
            "read_write_device",
            self._rw_expected_field(13),
            rw_id=13,
            context=status,
            rw=1,
        )
        await self.async_send_and_wait(
            "read_write_device",
            self._rw_expected_field(12),
            rw_id=12,
            context=mode,
            rw=1,
        )

    async def async_read_wildlife_safety(self) -> None:
        """Read current wildlife safety status and mode from device."""
        await self.async_send_and_wait(
            "read_write_device", self._rw_expected_field(13), rw_id=13, context=0, rw=0
        )
        await self.async_send_and_wait(
            "read_write_device", self._rw_expected_field(12), rw_id=12, context=0, rw=0
        )

    async def async_set_turning_mode(self, context: int) -> None:
        """Set turning mode."""
        await self.async_send_and_wait(
            "turning_mode", self._rw_expected_field(6), context=context
        )

    async def async_read_turning_mode(self) -> None:
        """Read current turning mode from device."""
        await self.async_send_and_wait(
            "read_write_device", self._rw_expected_field(6), rw_id=6, context=0, rw=0
        )

    async def async_blade_height(self, height: int) -> int:
        """Set blade height."""
        await self.async_send_and_wait(
            "set_blade_height", "toapp_knife_status_change", height=height
        )
        return height

    async def async_set_cutter_speed(self, mode: int) -> None:
        """Set cutter speed."""
        await self.async_send_and_wait(
            "set_cutter_mode", "cutter_mode_ctrl_by_hand", cutter_mode=mode
        )

    async def async_read_cutter_mode(self) -> None:
        """Query the current cutter mode and live RPM from the device."""
        await self.async_send_and_wait("get_cutter_mode", "current_cutter_mode")

    async def async_reset_blade_warning_time(self) -> None:
        """Reset blade used time to zero."""
        await self.async_send_and_wait(
            "reset_blade_time", "todev_reset_blade_used_time_status"
        )

    async def async_set_blade_warning_time(self, hours: int) -> None:
        """Set the blade warning time in hours."""
        await self.async_send_command("set_blade_warning_time", hours=hours)

    async def async_set_speed(self, speed: float) -> None:
        """Set working speed."""
        await self.async_send_and_wait(
            "set_speed", "bidire_speed_read_set", speed=speed
        )

    async def async_leave_dock(self) -> None:
        """Leave dock."""
        await self.send_command_and_update("leave_dock", "todev_taskctrl_ack")

    async def async_cancel_task(self) -> None:
        """Cancel task."""
        await self.send_command_and_update("cancel_job", "todev_taskctrl_ack")

    async def _async_ensure_ble_client(self) -> None:
        """Attach a BLE transport if we have an address but no client yet.

        Called before movement commands that prefer BLE so that a freshly
        discovered device (or one that was out of range at startup) gets a
        transport without waiting for the next full coordinator refresh.

        Short-circuits when the registered BLETransport already has the same
        BLEDevice address — avoids re-wiring on every 30 min refresh tick.
        Per-advertisement freshness is handled by the bluetooth callback in
        ``__init__.py``; this method only covers the case where no transport
        was wired (e.g. mower out of range at integration startup).
        """

        if not self._bluetooth_enabled:
            return

        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return
        ble_mac = device.mower_state.ble_mac
        if not ble_mac:
            return
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return

        # If a BLE transport already exists and has the same address cached, do nothing.
        # The per-advertisement callback (_ble_seen) handles routine refreshes.
        if ble := handle.get_transport(TransportType.BLE):
            if not ble.is_connected:
                await ble.connect()

            if ble.is_connected:
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
        """Fetch area names and wait for the toapp_all_hash_name response."""
        await self.async_send_and_wait(
            "get_area_name_list",
            "toapp_all_hash_name",
            device_id=self.device.iot_id,
        )

    async def async_set_area_name(self, hash_id: int, name: str) -> None:
        """Push a user-edited area name to the device.

        The device acks with a single toapp_map_name_msg (hash + name) which the
        pymammotion reducer applies to map.area_name, so no local write-back is
        needed here.
        """
        await self.async_send_and_wait(
            "set_area_name",
            "toapp_map_name_msg",
            device_id=self.device.iot_id,
            hash_id=hash_id,
            name=name,
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

    async def send_command_and_update(
        self, command_str: str, response: str | None = None, **kwargs: Any
    ) -> None:
        """Send command and update."""
        if response is not None:
            await self.async_send_and_wait(command_str, response, **kwargs)
        else:
            await self.async_send_command(command_str, **kwargs)
        await self.async_get_reports(count=5)

    async def async_request_report_snapshot(self) -> None:
        """Fire a one-shot count=1 snapshot; no-op while BLE stream is active."""
        await self.manager.request_report_snapshot(self.device_name)

    async def async_start_report_stream(self, duration_ms: int = 300_000) -> None:
        """Start a transient continuous report window via the library."""
        await self.manager.start_report_stream(self.device_name, duration_ms)

    async def async_get_reports(self, count: int = 5) -> None:
        """Get reports from the device."""
        await self.manager.request_reports(self.device_name, count=count)

    async def async_ensure_fresh_state(self) -> None:
        """Fire a one-shot snapshot if device state is older than 2 minutes."""
        await self.manager.ensure_fresh_state(self.device_name, max_age_s=120.0)

    async def send_svg_command(self, svg_message: SvgMessage) -> int | None:
        """Send an SVG tile to the device using the multi-frame saga protocol.

        Chunks *svg_message* into 500-character frames and sends them one at a
        time, waiting for a per-frame device ACK after each.  Returns the
        device-assigned ``data_hash`` for use in subsequent UPDATE or DELETE
        operations.

        Args:
            svg_message: Fully-populated message from
                         :func:`~pymammotion.utility.svg.build_svg_for_area` or
                         :func:`~pymammotion.utility.svg.build_svg_update`.

        Returns:
            Device-assigned ``data_hash``, or ``None`` on failure.

        """

        chunks = chunk_svg_messages(svg_message)
        return await self.manager.send_svg(self.device_name, chunks)

    def generate_route_information(
        self, operation_settings: OperationSettings
    ) -> GenerateRouteInformation:
        """Generate route information."""
        device: MowingDevice = cast(MowingDevice, self.data)
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

        if work := cast(MowingDevice, self.data).work:
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

    # ------------------------------------------------------------------
    # Mower task CRUD — backed by NavPlanJobSet on the wire.
    # All helpers look up the existing Plan from ``self.data.map.plan`` so
    # round-trip operations (enable / rename / edit / copy) preserve the
    # rest of the plan (reserved bytes, recurrence, areas, …) verbatim.
    # See ``docs/tasks_and_schedules.md`` § 1.
    # ------------------------------------------------------------------

    def _lookup_mower_plan(self, plan_id: str) -> Plan:
        """Return the stored mower Plan keyed by ``plan_id`` or raise."""
        plan = cast(MowingDevice, self.data).map.plan.get(plan_id)
        if plan is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="task_not_found",
                translation_placeholders={"plan_id": plan_id},
            )
        return plan

    async def async_create_mower_task(self, plan: Plan) -> None:
        """Create a brand-new mower schedule with a freshly generated plan_id.

        Caller passes a fully-populated Plan **without** a plan_id; this
        helper assigns one via :func:`new_mower_plan_id` so the device
        treats the write as a create rather than an edit.
        """
        plan_with_id = dataclasses.replace(plan, plan_id=new_mower_plan_id())
        await self.async_send_command("create_plan", plan=plan_with_id)

    async def async_edit_mower_task(self, plan: Plan) -> None:
        """Edit an existing mower schedule (``sub_cmd=4``)."""
        await self.async_send_command("edit_plan", plan=plan)

    async def async_rename_mower_task(self, plan_id: str, new_name: str) -> None:
        """Rename the mower schedule identified by ``plan_id`` to ``new_name``."""
        plan = self._lookup_mower_plan(plan_id)
        await self.async_send_command("rename_plan", plan=plan, new_name=new_name)

    async def async_set_mower_task_enabled(self, plan_id: str, enabled: bool) -> None:
        """Flip the enable flag (``reserved[2]``) on an existing mower schedule.

        The existing plan is round-tripped verbatim so the other reserved
        bytes (knife height, edge mode, …) are preserved.
        """
        plan = self._lookup_mower_plan(plan_id)
        await self.async_send_command("enable_plan", plan=plan, enabled=enabled)

    async def async_delete_mower_task(self, plan_id: str) -> None:
        """Delete the mower schedule identified by ``plan_id`` (``sub_cmd=3``)."""
        await self.async_send_command("delete_plan_by_id", plan_id=plan_id)

    async def async_copy_mower_task(
        self, plan_id: str, new_name: str | None = None
    ) -> None:
        """Duplicate the mower schedule under a new id + auto-generated name.

        Reuses :func:`make_copy_name` against the currently stored plans so
        successive copies produce ``Copy-1, Copy-2, …`` without collision.
        """
        plan = self._lookup_mower_plan(plan_id)
        existing_names = {
            p.task_name for p in cast(MowingDevice, self.data).map.plan.values()
        }
        resolved_name = new_name or make_copy_name(existing_names)
        await self.async_send_command(
            "copy_plan",
            plan=plan,
            new_name=resolved_name,
            new_plan_id=new_mower_plan_id(),
        )

    async def async_refresh_mower_tasks(self) -> None:
        """Re-fetch the mower schedule list via :class:`PlanFetchSaga`."""
        await self.manager.start_plan_sync(self.device_name)

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
        _mdata = cast(MowingDevice, self.data)
        if (
            int(_mdata.report_data.work.bp_hash) in _mdata.work.zone_hashs
            and (_mdata.report_data.work.area >> 16) != 100
        ):
            await self.async_modify_plan_route(self.operation_settings)

    async def async_restore_data(self) -> None:
        """Restore saved data."""
        store: MammotionConfigStore = MammotionConfigStore(
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

    async def async_save_data(self, data: MowingDevice | PoolCleanerDevice) -> None:
        """Store data."""
        store: Store = Store(
            self.hass, version=1, minor_version=2, key=self.device_name
        )
        await store.async_save(data.to_dict())

    async def remove_saved_data(self) -> None:
        """Remove saved coordinator data from persistent storage."""
        store = Store(self.hass, version=1, minor_version=2, key=self.device_name)
        await store.async_remove()

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
        if device.mower_state.ble_mac != "" and handle is not None:
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
                    handle.subscribe_shutdown(self._guarded(self._on_device_shutdown)),
                ]
            )

    async def _on_device_shutdown(self, event: DeviceShutdownEvent) -> None:
        """React to a device-initiated power-off notification.

        The handle has already set mqtt_reported_offline=True (blocking further
        sends) and emitted a state-changed snapshot.  We force an immediate HA
        state write here so the entity availability reflects the shutdown before
        the debounce window or the next MQTT heartbeat timeout.
        """
        LOGGER.debug(
            "%s: device power-off notification (power_type=%d)",
            self.device_name,
            event.power_type,
        )
        self.async_set_updated_data(
            self.manager.mower(self.device_name).state_machine.current.raw
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

    def subscribe_map_updated(self, handler: Callable[[], None]) -> None:
        """Subscribe *handler* to map-updated events from the device handle.

        Fires only when ``toapp_all_hash_name`` is received or a ``MapFetchSaga``
        completes — not on every telemetry tick.  The subscription is kept alive
        for the lifetime of the coordinator and cancelled on shutdown.
        """
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return

        async def _on_map_updated() -> None:
            if not self.hass.is_stopping:
                handler()

        self._subscriptions.append(handle.subscribe_map_updated(_on_map_updated))

    async def async_shutdown(self) -> None:
        """Cancel all RAII subscriptions and delegate to HA coordinator shutdown."""
        for sub in self._subscriptions:
            sub.cancel()
        self._subscriptions.clear()
        await super().async_shutdown()

    async def _on_state_changed(self, snapshot: DeviceSnapshot) -> None:
        """Push updated device data to HA."""
        self.device.online = True
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
        _mower_data = cast(MowingDevice, self.data)
        if area_data := _mower_data.map.area.get(area_hash):
            area_frame = area_data.data[0] if len(area_data.data) > 0 else None
            if area_frame is not None:
                area_name: AreaHashNameList | None = next(
                    (
                        area
                        for area in _mower_data.map.area_name
                        if area.hash == area_frame.hash
                    ),
                    None,
                )
                name = area_name.name if area_name is not None else None
        else:
            return "path"

        return name if name else f"area {area_hash}"

    @property
    def map_sync_status(self) -> str:
        """Return the current map-sync status for diagnostics.

        One of :data:`MAP_SYNC_STATUSES`:

        * ``syncing`` — an exclusive sync saga (the map fetch) is running on
          the device command queue, so the cached map is mid-refresh.
        * ``synced`` — our local map fully matches the device's current area
          set (``map.is_map_synced`` against the latest reported ``bol_hash``).
        * ``out_of_sync`` — neither of the above: the cached map is stale or
          incomplete and a fresh ``async_sync_maps()`` is needed.
        """
        handle = self.manager.mower(self.device_name)
        if handle is not None and handle.queue.is_saga_active:
            return "syncing"

        if self.data is None:
            return "out_of_sync"

        mower_data = cast(MowingDevice, self.data)
        locations = mower_data.report_data.locations
        bol_hash = locations[0].bol_hash if locations else 0
        if mower_data.map.is_map_synced(bol_hash):
            return "synced"
        return "out_of_sync"


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

        self._on_stop: list[CALLBACK_TYPE] = []

        self.poll_debouncer = Debouncer(
            hass,
            LOGGER,
            cooldown=60,
            immediate=True,
            function=self._add_ble_device,
            background=True,
        )

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Handle a bluetooth advertisement for this mower's MAC.

        Two responsibilities:

        1. Cache the latest ``service_info`` for downstream use (RSSI gates,
           freshness checks, etc.).
        2. Push the freshest ``BLEDevice`` into the existing BLETransport so
           ``bleak_retry_connector``'s ``ble_device_callback`` always has the
           most recent advertisement.  This is a synchronous pointer-swap
           (``BLETransport.set_ble_device``) — no event-loop work needed,
           safe to run in this ``@callback``-decorated handler.

        Initial transport wire-up (``add_ble_to_device``) is handled by
        :func:`_attach_ble_to_mower` in ``__init__.py``, which has access to
        the ``stay_connected_ble`` config flag.  Once the transport exists,
        every subsequent advertisement flows through this fast path.
        """
        self.service_info = service_info

        self.poll_debouncer.async_schedule_call()

    def _add_ble_device(self) -> None:
        if not self.service_info or not self._bluetooth_enabled:
            return
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return
        ble = handle.get_transport(TransportType.BLE)
        if ble is None:
            self.hass.create_task(self._async_ensure_ble_client())

        if ble := handle.get_transport(TransportType.BLE):
            if not ble.is_connected and self.data.enabled:
                cast(BLETransport, ble).set_ble_device(
                    self.service_info.device, self.service_info.rssi
                )
                self.hass.create_task(ble.connect())

    async def async_set_bluetooth_enabled(self, enabled: bool) -> None:
        """Enable or disable Bluetooth, reconnecting if re-enabled."""
        await super().async_set_bluetooth_enabled(enabled)
        if enabled:
            self._add_ble_device()

    @callback
    def _async_start(self) -> None:
        """Start the callbacks."""
        if self.data.mower_state.ble_mac != "":
            self._on_stop.append(
                async_register_callback(
                    self.hass,
                    self._async_handle_bluetooth_event,
                    BluetoothCallbackMatcher(
                        address=self.data.mower_state.ble_mac, connectable=True
                    ),
                    BluetoothScanningMode.ACTIVE,
                )
            )

    @callback
    def _async_stop(self) -> None:
        """Stop the callbacks."""
        for unsub in self._on_stop:
            unsub()
        self._on_stop.clear()

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
        await self.async_save_data(device)

        if self.data.mower_state.ble_mac != "" and len(self._on_stop) == 0:
            self._on_stop.append(
                async_register_callback(
                    self.hass,
                    self._async_handle_bluetooth_event,
                    BluetoothCallbackMatcher(
                        address=self.data.mower_state.ble_mac, connectable=True
                    ),
                    BluetoothScanningMode.ACTIVE,
                )
            )

        if handle := self.manager.mower(self.device_name):
            if ble := handle.get_transport(TransportType.BLE):
                if (
                    handle.prefer_ble
                    and ble.is_usable
                    and not ble.is_connected
                    and self._bluetooth_enabled
                ):
                    try:
                        await ble.connect()
                    except BLEUnavailableError as exc:
                        LOGGER.debug(
                            "BLE unavailable for %s during update — continuing via cloud: %s",
                            self.device_name,
                            exc,
                        )

        return device

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

    async def _async_setup(self) -> None:
        await super()._async_setup()

        # Common commands for all device types
        commands = [
            ("send_todev_ble_sync", {"sync_type": 3}),
            ("async_read_rain_detection", {}),
            ("async_read_sidelight", {}),
            ("async_read_turning_mode", {}),
            ("async_read_traversal_mode", {}),
        ]

        # Add device-specific commands
        if DeviceType.is_mini_or_x_series(self.device_name):
            commands.extend(
                [
                    ("async_read_manual_light", {}),
                    ("async_read_night_light", {}),
                    ("async_read_cutter_mode", {}),
                ]
            )

        if DeviceType.is_luba_pro(self.device_name):
            commands.extend(
                [
                    ("async_fetch_audio_config", {}),
                    ("async_read_wildlife_safety", {}),
                ]
            )

        # Final command for all devices
        commands.append(("async_request_report_snapshot", {}))

        # Execute all commands with unified exception handling
        for command_name, kwargs in commands:
            try:
                command_method = getattr(self, command_name, None)
                if command_method is None:
                    command_method = self.async_send_command
                    await command_method(command_name, **kwargs)
                else:
                    await command_method(**kwargs)
            except (
                DeviceOfflineException,
                NoTransportAvailableError,
                CommandTimeoutError,
                ConcurrentRequestError,
                BLEUnavailableError,
            ) as exc:
                LOGGER.debug(f"Command {command_name} failed with exception: {exc}")

        # Watch sys_status changes so we can refresh the full status when the
        # device transitions states.  Skipped when the BLE polling loop is
        # already feeding a continuous count=0 stream — the stream is fresher
        # than any count=1 poll we could fire.
        if (handle := self.manager.mower(self.device_name)) is not None:
            handle.watch_field(
                lambda s: s.raw.report_data.dev.sys_status,
                self._on_sys_status_changed_refresh,
            )

    async def _on_sys_status_changed_refresh(self, sys_status: int) -> None:
        """Trigger a one-shot count=1 poll on sys_status transitions when not streaming."""
        try:
            await self.async_request_report_snapshot()
        except (DeviceOfflineException, NoTransportAvailableError):
            LOGGER.debug(
                "report-coordinator [%s]: skipping sys_status refresh — device offline / no transport",
                self.device_name,
            )


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
        self._prev_sys_status: int | None = None

    def get_coordinator_data(self, device: MowingDevice) -> Maintain:
        """Get coordinator data."""
        return device.report_data.maintenance

    async def _on_state_changed(self, snapshot: DeviceSnapshot) -> None:
        data = cast(MowerDevice, snapshot.raw)
        self.async_set_updated_data(data.report_data.maintenance)

    async def _on_sys_status_changed(self, sys_status: int) -> None:
        """Fetch maintenance data when the mower transitions from working to ready."""
        was_working = self._prev_sys_status in MOWING_ACTIVE_MODES
        self._prev_sys_status = sys_status
        if was_working and sys_status == WorkMode.MODE_READY:
            try:
                await self.async_send_command("get_maintenance")
            except (DeviceOfflineException, GatewayTimeoutException):
                pass

    async def _async_update_data(self) -> Maintain:
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data

        _dev = self.manager.get_device_by_name(self.device.device_name)
        assert _dev is not None
        return _dev.report_data.maintenance

    async def _async_setup(self) -> None:
        """Set up maintenance coordinator."""
        await super()._async_setup()

        if handle := self.manager.mower(self.device_name):
            handle.watch_field(
                lambda s: s.raw.report_data.dev.sys_status,
                self._on_sys_status_changed,
            )

        try:
            await self.async_send_command("get_maintenance")
            await self.async_send_and_wait(
                "read_job_do_not_disturb", "todev_unable_time_set"
            )
        except (DeviceOfflineException, GatewayTimeoutException):
            pass


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
        assert device is not None
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

        if handle is not None and self.has_cloud_account:
            http = self.manager.mammotion_http
            if http is not None:
                ota_info = await http.get_device_ota_firmware([handle.iot_id])
                LOGGER.debug("OTA info: %s", ota_info.data)
                if check_versions := ota_info.data:
                    for check_version in check_versions:
                        if check_version.device_id == handle.iot_id:
                            device.apply_version_check(check_version)

        if device.mower_state.model_id != "":
            self.update_interval = DEVICE_VERSION_INTERVAL

        return device

    async def _async_setup(self) -> None:
        """Set up device version coordinator."""
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
            if handle is not None and self.has_cloud_account:
                http = self.manager.mammotion_http
                if http is not None:
                    ota_info = await http.get_device_ota_firmware([handle.iot_id])
                    device = self.manager.get_device_by_name(self.device_name)
                    if device is not None and (check_versions := ota_info.data):
                        for check_version in check_versions:
                            if check_version.device_id == handle.iot_id:
                                device.apply_version_check(check_version)

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
        assert device is not None

        try:
            # RTK/dock lat are radians with an exact-0.0 "unset" sentinel — compare to 0.0,
            # not round(lat, 0), which would treat everything within ~0.5 rad (~28°) of the
            # equator as unset and re-fetch the location on every update.
            if (
                device.location.RTK.latitude == 0.0
                or device.location.dock.latitude == 0.0
            ):
                await self.async_rtk_dock_location()

            bol_hash = (
                device.report_data.locations[0].bol_hash
                if device.report_data.locations
                else 0
            )
            if not device.map.is_map_synced(bol_hash):
                await self.manager.start_map_sync(self.device_name)

        except DeviceOfflineException as ex:
            if ex.iot_id == self.device.iot_id:
                self.device_offline(device)
                return device.mower_state
        except GatewayTimeoutException:
            pass
        except (ConcurrentRequestError, NoTransportAvailableError):
            pass

        _d = self.manager.get_device_by_name(self.device_name)
        assert _d is not None
        return _d.mower_state

    async def _async_setup(self) -> None:
        """Set up coordinator with initial call to get map data."""
        await super()._async_setup()
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return

        if not device.enabled or not device.online:
            return
        try:
            await self.async_rtk_dock_location()
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
            event_params: DeviceNotificationEventParams = cast(
                DeviceNotificationEventParams, event.params
            )
            # '[{"c":-2801,"ct":1,"ft":1731493734000},{"c":-1008,"ct":1,"ft":1731493734000}]'
            try:
                warning_event = json.loads(event_params.value.data)
                LOGGER.debug("warning event %s", warning_event)
                await self._async_update_data()
                if device := self.manager.get_device_by_name(self.device_name):
                    self.async_set_updated_data(device)
            except json.JSONDecodeError:
                """Failed to parse warning event."""

    def get_error_code(self, number: int) -> int:
        """Get error code from an error code list."""
        try:
            return int(abs(next(iter(self.data.errors.err_code_list))))
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
        assert device is not None
        try:
            if not device.errors.error_codes and self.has_cloud_account:
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
        if device is None:
            return
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
            if not device.errors.error_codes and self.has_cloud_account:
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

        if self.has_cloud_account:
            http = self.manager.mammotion_http
            if http is not None:
                try:
                    ota_info = await http.get_device_ota_firmware([self.device.iot_id])
                    if check_versions := ota_info.data:
                        for check_version in check_versions:
                            if check_version.device_id == self.device.iot_id:
                                self.data.apply_version_check(check_version)
                except ReLoginRequiredError as err:
                    raise ConfigEntryAuthFailed(
                        f"Re-authentication required for Mammotion account: {err}"
                    ) from err
                except (DeviceOfflineException, GatewayTimeoutException):
                    pass

        return self.data

    async def async_shutdown(self) -> None:
        """Cancel all RAII subscriptions and delegate to HA coordinator shutdown."""
        for sub in self._subscriptions:
            sub.cancel()
        self._subscriptions.clear()
        await super().async_shutdown()

    async def _async_setup(self) -> None:
        """Set up RTK device subscriptions and fetch one-time HTTP data."""
        await super()._async_setup()
        if handle := self.manager.rtk_device(self.device_name):
            updated = handle.snapshot.raw
            updated.product_key = self.device.product_key
            updated.iot_id = self.device.iot_id
            updated.name = self.device.device_name
            snapshot, _ = handle.state_machine.apply(updated, handle.availability)

        if self.data.lat != 0:
            return

        if self.has_cloud_account:
            # Fetch lora version — only available via HTTP, not MQTT/protobuf.
            await self.manager.fetch_rtk_lora_info(self.device_name)

            if (
                gateway := self.manager.cloud_gateway
            ) and DeviceType.is_aliyun_product_key(self.data.product_key):
                await self.manager.fetch_rtk_properties(self.device_name)
                await gateway.get_device_status(self.device.iot_id)
        await self.async_send_command("send_todev_ble_sync", sync_type=3)
        await self.async_request_report_snapshot()
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


class MammotionSpinoCoordinator(MammotionBaseUpdateCoordinator[PoolCleanerDevice]):
    """Mammotion DataUpdateCoordinator for Spino pool cleaner devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MammotionConfigEntry,
        device: Device,
        mammotion: MammotionClient,
        unique_name: str | None = None,
    ) -> None:
        """Initialize spino mammotion data updater."""
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            device=device,
            mammotion=mammotion,
            update_interval=SPINO_INTERVAL,
            unique_name=unique_name,
        )

    async def _async_setup(self) -> None:
        """Subscribe to device events, then read the initial toggle states once.

        The buzzer/turbo/platform/waterline toggles aren't part of the regular
        push — the device only emits a ``bidire_comm_cmd`` for them in response
        to a read or write.  Issue one read per toggle here to seed initial
        state; subsequent changes (from our writes or the Mammotion app) arrive
        as ``bidire_comm_cmd`` responses applied by ``PoolStateReducer`` and
        pushed to entities via the inherited ``_on_state_changed`` callback.
        """
        await super()._async_setup()
        # Start the status report stream so the device pushes dev_statue_t
        # (sys_status / work_mode / battery) — the pool cleaner doesn't report
        # unsolicited otherwise. See get_report_cfg_spino / async_subscribe_status.

        try:
            with contextlib.suppress(
                GatewayTimeoutException,
                NoTransportAvailableError,
                HomeAssistantError,
            ):
                await self.async_subscribe_status()
            for toggle in SpinoToggle:
                with contextlib.suppress(
                    GatewayTimeoutException,
                    NoTransportAvailableError,
                ):
                    await self.async_send_and_wait(
                        "read_write_device",
                        "bidire_comm_cmd",
                        rw_id=int(toggle),
                        context=0,
                        rw=0,
                    )
        except DeviceOfflineException:
            self.device.online = False

    async def get_coordinator_data(
        self, device: PoolCleanerDevice
    ) -> PoolCleanerDevice:
        """Return the current pool cleaner state tracked by this coordinator."""
        return self.data

    async def async_restore_data(self) -> None:
        """Restore saved data."""
        store = MammotionConfigStore(
            self.hass, version=1, minor_version=2, key=self.device_name
        )
        restored_data: Mapping[str, Any] | None = await store.async_load()

        handle = self.manager.mower(self.device_name)

        if restored_data is None:
            empty = PoolCleanerDevice()
            self.data = empty
            if handle is not None:
                handle.restore_device(empty)
            return

        try:
            spino_state = PoolCleanerDevice().from_dict(restored_data)
            if handle is not None:
                handle.restore_device(spino_state)
                self.data = spino_state
        except InvalidFieldValue:
            empty = PoolCleanerDevice()
            self.data = empty
            if handle is not None:
                handle.restore_device(empty)

    def get_error_code(self) -> int:
        """Return the absolute error code of the most recent fault, or 0."""
        try:
            return int(abs(self.data.pool_state.error_log[0].code))
        except IndexError:
            return 0

    def get_error_time(self) -> datetime.datetime | None:
        """Return the timestamp of the most recent fault as a UTC datetime, or None."""
        try:
            return datetime.datetime.fromtimestamp(
                self.data.pool_state.error_log[0].timestamp, datetime.UTC
            )
        except IndexError:
            return None

    def get_error_message(self) -> str:
        """Return a human-readable description of the most recent fault."""
        try:
            error_code = abs(self.data.pool_state.error_log[0].code)
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
        except IndexError:
            return "No Error"
        except KeyError:
            return "Error message not found"

    async def _async_update_data(self) -> PoolCleanerDevice:
        """Return current pool cleaner state from the device handle.

        Runtime state (sys_status, work_mode, battery, settings, map) is pushed
        into the state machine by ``PoolStateReducer`` as MQTT frames arrive, so
        the only polling work here is the HTTP OTA firmware check, which is not
        pushed over MQTT.
        """
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return self.data

        if self.has_cloud_account:
            http = self.manager.mammotion_http
            if http is not None:
                try:
                    ota_info = await http.get_device_ota_firmware([self.device.iot_id])
                    if check_versions := ota_info.data:
                        for check_version in check_versions:
                            if check_version.device_id == self.device.iot_id:
                                self.data.apply_version_check(check_version)
                    if not self.data.errors.error_codes:
                        self.data.errors.error_codes = await http.get_all_error_codes()
                except ReLoginRequiredError as err:
                    raise ConfigEntryAuthFailed(
                        f"Re-authentication required for Mammotion account: {err}"
                    ) from err
                except (DeviceOfflineException, GatewayTimeoutException):
                    pass

        await self.async_save_data(self.data)

        return self.data

    async def update_firmware(self, version: str) -> None:
        """Update firmware."""
        http = self.manager.mammotion_http
        if http is not None:
            await http.start_ota_upgrade(self.device.iot_id, version)

    # === Pool cleaner control helpers (called by control entities) ===

    async def async_subscribe_status(self) -> None:
        """Start the Spino status report stream (called once at setup).

        Subscribes to RIT_CONNECT + RIT_DEV_STA with count=0 (continuous) so the
        device pushes dev_statue_t frames; PoolStateReducer applies them.
        """
        await self.async_send_command("get_report_cfg_spino", count=1)

    async def async_request_status(self) -> None:
        """One-shot Spino status poll, backing the refresh-status button."""
        await self.async_send_command("get_report_cfg_spino", count=1)

    async def async_set_work_mode(self, work_mode: int) -> None:
        """Set the Spino cleaning work mode."""
        await self.async_send_command("set_swimming_work_mode", work_mode=work_mode)

    async def async_set_wall_material(self, material: int) -> None:
        """Set the pool wall material."""
        await self.async_send_command("sp_environment_update", material=material)

    async def async_set_bottom_type(self, bottom_type: int) -> None:
        """Set the pool bottom shape type."""
        await self.async_send_command("sp_set_bottom_type", bottom_type=bottom_type)

    async def async_set_floor_speed(self, speed: float) -> None:
        """Set the pool floor cleaning speed."""
        await self.async_send_command("sp_speed_update", speed=speed)

    async def async_fetch_pool_map(self) -> None:
        """Request the pool boundary map from the device."""
        await self.async_send_command("get_sp_map")

    async def async_fetch_pool_line(self) -> None:
        """Request the pool cleaning route from the device."""
        await self.async_send_command("get_sp_line")

    # ------------------------------------------------------------------
    # Spino task CRUD — backed by spino_ctrl.PlanJobSet on the wire.
    # See ``docs/tasks_and_schedules.md`` § 2.  All ``enabled`` arguments
    # are in NATURAL orientation; the builder inverts at the boundary.
    # ------------------------------------------------------------------

    def _lookup_spino_plan(self, jobid: int) -> PoolPlan:
        """Return the stored Spino PoolPlan keyed by ``jobid`` or raise."""
        plan = self.data.plans.get(jobid)
        if plan is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="task_not_found",
                translation_placeholders={"plan_id": str(jobid)},
            )
        return plan

    @staticmethod
    def _new_spino_jobid() -> int:
        """Generate a fresh 64-bit non-zero ``jobid`` for a new Spino plan.

        ``secrets.randbits(63)`` keeps the high bit clear (fits in a signed
        uint64-as-int comfortably); ``| 1`` avoids the all-zero corner.
        """
        return secrets.randbits(63) | 1

    async def async_create_spino_task(self, plan: PoolPlan) -> None:
        """Create a brand-new Spino schedule with a freshly generated jobid."""
        plan_with_id = dataclasses.replace(plan, jobid=self._new_spino_jobid())
        await self.async_send_command("create_spino_plan", plan=plan_with_id)

    async def async_edit_spino_task(self, plan: PoolPlan) -> None:
        """Edit an existing Spino schedule (``cmd = EDIT = 4``)."""
        await self.async_send_command("edit_spino_plan", plan=plan)

    async def async_rename_spino_task(self, jobid: int, new_name: str) -> None:
        """Rename the Spino schedule identified by ``jobid`` to ``new_name``."""
        plan = self._lookup_spino_plan(jobid)
        await self.async_send_command("rename_spino_plan", plan=plan, new_name=new_name)

    async def async_set_spino_task_enabled(self, jobid: int, enabled: bool) -> None:
        """Flip the enabled flag on an existing Spino schedule.

        Round-trips the stored plan; the wire inversion (``enable = 0 if
        enabled else 1``) happens in the pymammotion builder.
        """
        plan = self._lookup_spino_plan(jobid)
        await self.async_send_command("enable_spino_plan", plan=plan, enabled=enabled)

    async def async_delete_spino_task(self, jobid: int) -> None:
        """Delete the Spino schedule identified by ``jobid``."""
        await self.async_send_command("delete_spino_plan", jobid=jobid)

    async def async_copy_spino_task(
        self, jobid: int, new_name: str | None = None
    ) -> None:
        """Duplicate the Spino schedule under a new jobid + auto-generated name."""
        plan = self._lookup_spino_plan(jobid)
        existing_names = {p.jobname for p in self.data.plans.values()}
        resolved_name = new_name or make_copy_name(existing_names)
        await self.async_send_command(
            "copy_spino_plan",
            plan=plan,
            new_name=resolved_name,
            new_jobid=self._new_spino_jobid(),
        )

    async def async_refresh_spino_tasks(self) -> None:
        """Re-fetch every Spino schedule via :class:`SpinoPlanFetchSaga`.

        Used after ``delete_all`` (no per-plan echo) and on user request via
        the schedule-refresh service.
        """
        await self.manager.start_spino_plan_sync(self.device_name)

    async def async_set_pool_toggle(self, toggle: SpinoToggle, enabled: bool) -> None:
        """Write a Spino on/off toggle (buzzer / turbo / platform / waterline).

        Uses the generic ``read_write_device`` (``allpowerfullRW``) command: the
        toggle id with ``context`` 0/1 and ``rw=1`` (write).
        """
        await self.async_send_command(
            "read_write_device", rw_id=int(toggle), context=int(enabled), rw=1
        )
