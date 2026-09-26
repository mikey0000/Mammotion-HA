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
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Any, Protocol, cast

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
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
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
from pymammotion.const import MAMMOTION_API_DOMAIN
from pymammotion.data.error_codes import get_error_info, table_language
from pymammotion.data.model import GenerateRouteInformation
from pymammotion.data.model.device import (
    MowerDevice,
    MowerInfo,
    MowingDevice,
    PoolCleanerDevice,
    RTKBaseStationDevice,
)
from pymammotion.data.model.device_config import OperationSettings, create_path_order
from pymammotion.data.model.enums import CollectorState, DumpState
from pymammotion.data.model.hash_list import Plan, SvgMessage
from pymammotion.data.model.pool_state import PoolPlan, SpinoToggle
from pymammotion.data.model.report_info import Maintain, NetUsedType
from pymammotion.data.mqtt.event import DeviceNotificationEventParams, ThingEventMessage
from pymammotion.data.mqtt.properties import ThingPropertiesMessage
from pymammotion.data.mqtt.status import StatusType, ThingStatusMessage
from pymammotion.http.model.camera_stream import (
    StreamSubscriptionResponse,
)
from pymammotion.http.model.http import ErrorInfo, Response, UnauthorizedExceptionError
from pymammotion.http.model.map_backup import (
    BackupMapItem,
    BackupMapProgress,
    BackupMapResult,
    BackupProgressType,
)
from pymammotion.http.model.product_params import ProductParam, ProductParamData
from pymammotion.http.model.response_factory import response_factory
from pymammotion.mammotion.commands.mammotion_command import MammotionCommand
from pymammotion.messaging.command_queue import Priority
from pymammotion.proto import MulSex
from pymammotion.state.device_state import (
    DeviceNotification,
    DeviceShutdownEvent,
    DeviceSnapshot,
)
from pymammotion.transport.base import (
    BLEUnavailableError,
    CommandTimeoutError,
    ConcurrentRequestError,
    LoginFailedError,
    NoTransportAvailableError,
    ReLoginRequiredError,
    SessionExpiredError,
    Subscription,
    TransportError,
    TransportRateLimitedError,
    TransportType,
    is_transient_network_error,
)
from pymammotion.utility.constant import MOWING_ACTIVE_MODES, WorkMode
from pymammotion.utility.device_type import DeviceType
from pymammotion.utility.plan_id import make_copy_name, new_mower_plan_id
from webrtc_models import RTCIceServer

from .agora_api import SERVICE_IDS, AgoraAPIClient, AgoraResponse
from .config import (
    TRANSPORT_BLUETOOTH,
    TRANSPORT_CLOUD,
    MammotionConfigStore,
    async_get_store,
)
from .const import (
    CONF_ACCOUNTNAME,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_MAMMOTION_DATA,
    DOMAIN,
    EXPIRED_CREDENTIAL_EXCEPTIONS,
    LOGGER,
    NO_REQUEST_MODES,
)
from .error_codes import async_refresh_error_codes

if TYPE_CHECKING:
    from pymammotion.device.handle import DeviceHandle
    from pymammotion.http.http import MammotionHTTP

    from . import MammotionConfigEntry


class WebRTCSessionControl(Protocol):
    """Teardown surface the WebRTC camera entity exposes to its coordinator."""

    async def async_teardown_stream(self, *, stop_device: bool = True) -> None:
        """Leave this camera's Agora session and optionally stop the encoder."""


def vision_camera_slots(device_name: str) -> int:
    """Return how many vision cameras the stream token should enable.

    The token request always carries three ``cameraStates`` slots, and the
    mower publishes slot ``n`` as Agora uid ``n + 1``.  Vision mowers expose
    two front cameras; Yuka adds a rear camera in slot 2.
    """
    if DeviceType.is_luba1(device_name):
        return 0
    if DeviceType.is_yuka(device_name):
        return 3
    return 2


MAINTENANCE_INTERVAL = timedelta(minutes=60)
DEFAULT_INTERVAL = timedelta(minutes=30)
REPORT_INTERVAL = timedelta(minutes=5)
DYNAMICS_LINE_INTERVAL = timedelta(seconds=10)
DEVICE_VERSION_INTERVAL = timedelta(weeks=1)
# How long a failed firmware check waits before another push may retry it.
FIRMWARE_CHECK_RETRY_INTERVAL = timedelta(hours=1)
MAP_INTERVAL = timedelta(minutes=60)
RTK_INTERVAL = timedelta(hours=5)
SPINO_INTERVAL = timedelta(weeks=1)

#: Wall-clock budget for the optional settings reads on the setup path.  They only
#: hydrate settings — the coordinator's data does not depend on any of them — but a
#: mower that answers none of them costs retries x send_timeout each, and if the loop
#: is still running when Home Assistant's setup window (SLOW_SETUP_MAX_WAIT, 300 s)
#: expires, HA *cancels* the config-entry task.  That surfaces as "Setup of config
#: entry ... cancelled" with a CancelledError from whichever read was in flight, which
#: reads like a library fault rather than the timeout it is.  See issue #859.
SETUP_COMMAND_BUDGET = timedelta(seconds=60)

#: The app's satellite-map alignment offset lives only on the phone; this is what
#: it sends when none was set.
MAP_BACKUP_CORRECTION_VALUE = '{"OffsetX":0.0,"OffsetY":0.0}'

#: How long to wait for the device to acknowledge the last SVG frame.  The saga itself
#: can run to its own 300 s ceiling on a bad link, but a service call should not block
#: that long — the transfer keeps going regardless, only the returned hash is given up.
SVG_SEND_TIMEOUT = timedelta(seconds=90)

# Possible states for ``MammotionReportUpdateCoordinator.map_sync_status`` and
# the ``map_sync_status`` diagnostic ENUM sensor that surfaces it.
MAP_SYNC_STATUSES = ("synced", "syncing", "out_of_sync")

# Cloud response code returned by the stream-subscription endpoint when the
# device is unreachable ("Device not responding. Please check the network
# connection").  Treated as a device-offline signal.
DEVICE_NOT_RESPONDING_CODE = 50504


#: The device echoes the reserved buffer back with every byte raised by ten —
#: the same quirk that made enabling a schedule corrupt it (Mammotion-HA #891).
#: Observed on a running job: b"\n\x0b\n\n\n\x12\x14(" decodes to
#: 0/1/0/0/0/8/10, where the 8 and 10 are exactly the constants
#: ``create_path_order`` writes, which is what confirms the offset.
_RESERVED_ECHO_OFFSET = 10
_RESERVED_ECHOED_BYTES = (0, 1, 2, 3, 4, 5, 6)


def _reserved_without_echo(reserved: str) -> str:
    """Undo the device's +10 echo so a re-issued route does not accumulate it."""
    raw = bytearray(reserved.encode("latin-1").ljust(8, b"\x00"))
    for index in _RESERVED_ECHOED_BYTES:
        raw[index] = max(raw[index] - _RESERVED_ECHO_OFFSET, 0)
    return raw.decode("latin-1")


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
        # Public because the camera platform populates it from Agora and the
        # camera entity reads it back; the refresh below keeps it current.
        self.ice_servers: list[RTCIceServer] = []
        self._agora_response = None
        # Set by the WebRTC camera entity so the start/stop_video services and
        # config-entry unload can drive the same teardown the frontend uses.
        self._webrtc_session_controls: dict[str, WebRTCSessionControl] = {}
        self._dual_camera_stream_available = False
        self._active_camera_sessions: dict[str, str] = {}
        self._camera_session_lock = asyncio.Lock()
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
        except KeyError, TypeError, ValueError:
            _user_account = 0
        self.commands = MammotionCommand(device.device_name, _user_account)
        self._subscriptions: list[Subscription] = []
        self.map_offset_lat: float = 0.0
        self.map_offset_lon: float = 0.0
        self._store: MammotionConfigStore = async_get_store(hass, config_entry)
        self._bring_up_done = False
        self._startup_reads_done = False
        self._bluetooth_enabled: bool = self._store.transport_enabled(
            self.device_name, TRANSPORT_BLUETOOTH
        )
        self._cloud_enabled: bool = self._store.transport_enabled(
            self.device_name, TRANSPORT_CLOUD
        )

        mower_device = self.manager.get_device_by_name(self.device_name)

        if self.data is None:
            self.data = mower_device

    @property
    def has_cloud_account(self) -> bool:
        """Return True if the entry is configured with a cloud account."""
        if CONF_HAS_CLOUD_ACCOUNT in self.config_entry.data:
            return bool(self.config_entry.data[CONF_HAS_CLOUD_ACCOUNT])
        return bool(self.account)

    @property
    def cloud_http_usable(self) -> bool:
        """Return True while the account's HTTP login is live and not awaiting re-authentication.

        Unlike :attr:`has_cloud_account` (entry configuration) this reflects the
        runtime state, so HTTP-only work is skipped — not failed — once the cloud
        side of the account has been quiesced while its mowers carry on over BLE.
        """
        return (
            self.manager.mammotion_http is not None
            and self.manager.reauth_required is None
        )

    def describe_error_code(self, code: int) -> dict[str, str] | None:
        """Return module, level, localised message and solution, and the display text, for a code.

        ``text`` is the one formatter for error strings — the error sensors and the
        notification event all read it — so the three can never drift.  The table
        is process-wide, so this works from any of a mower's coordinators
        regardless of what ``self.data`` holds, and falls back to the bundled
        table when no account has been fetched from.
        """
        error_info: ErrorInfo | None = get_error_info(code)
        if error_info is None:
            return None
        language = table_language(self.hass.config.language)
        message = (
            getattr(error_info, f"{language}_implication", "")
            or error_info.en_implication
        )
        solution = (
            getattr(error_info, f"{language}_solution", "") or error_info.en_solution
        )
        text = ""
        if message:
            text = f"{error_info.module}: {message}"
            if solution:
                text = f"{text}, {solution}"
        return {
            "module": error_info.module,
            "level": error_info.level,
            "message": message,
            "solution": solution,
            "text": text,
        }

    async def _async_refresh_error_codes(self) -> None:
        """Keep the process-wide error-code table current; cheap after the first call."""
        http = self.manager.mammotion_http if self.cloud_http_usable else None
        await self._cloud_api_call(async_refresh_error_codes(self.hass, http))

    async def async_bring_up(self) -> None:
        """Run the one-time setup hook, then refresh without raising.

        Home Assistant runs ``_async_setup`` only from the first-refresh path, which
        the background bring-up does not use.  The hook wires the push subscriptions,
        so it runs exactly once here; like Home Assistant's own guard, a failed setup
        marks the coordinator failed and skips the refresh.
        """
        if not self._bring_up_done:
            self._bring_up_done = True
            try:
                await self._async_setup()
            except Exception as exc:  # noqa: BLE001 — mirrors DataUpdateCoordinator's setup guard
                self.last_exception = exc
                self.last_update_success = False
                LOGGER.warning(
                    "%s: coordinator setup failed: %s", self.device_name, exc
                )
                return
        await self.async_refresh()

    @property
    def handle(self) -> DeviceHandle | None:
        """Return this device's DeviceHandle, or None when not registered."""
        return self.manager.mower(self.device_name)

    @property
    def has_ble(self) -> bool:
        """Return True when this device carries a BLE transport (works without any cloud)."""
        handle = self.handle
        return handle is not None and handle.has_transport(TransportType.BLE)

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
        request_dual = vision_camera_slots(self.device_name) > 1
        self._dual_camera_stream_available = False

        try:
            # Keep PyMammotion's normal path responsible for starting the video
            # encoder, including its firmware-aware start/stop behavior.
            stream_data = await self.manager.get_stream_subscription(
                self.device_name, self.device.iot_id
            )
            if request_dual:
                try:
                    dual_stream_data = await self._request_dual_camera_stream()
                except Exception as err:  # noqa: BLE001 — dual mode is optional
                    LOGGER.warning(
                        "Dual-camera token request failed (%s); using single camera",
                        type(err).__name__,
                    )
                else:
                    if (
                        dual_stream_data is not None
                        and dual_stream_data.data is not None
                    ):
                        stream_data = dual_stream_data
                        self._dual_camera_stream_available = True
                    elif dual_stream_data is None or dual_stream_data.code not in (
                        DEVICE_NOT_RESPONDING_CODE,
                        401,
                    ):
                        LOGGER.warning(
                            "Dual-camera token request was not accepted (code %s); "
                            "using single camera",
                            dual_stream_data.code
                            if dual_stream_data
                            else "no_response",
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
                        self.ice_servers = ice_servers
                        self._agora_response = agora_response
                        LOGGER.info(
                            "Retrieved %d ICE servers from Agora API",
                            len(ice_servers),
                        )
                except Exception:
                    LOGGER.exception("Failed to get ICE servers from Agora API")
                    self.ice_servers = []

            LOGGER.debug("Stream token refreshed successfully")
        except Exception:
            LOGGER.exception("Failed to refresh stream token")
        return (
            stream_data.data if stream_data is not None else None,
            self._agora_response,
        )

    async def _request_dual_camera_stream(self) -> Response[StreamSubscriptionResponse]:
        """Request every vision feed from the app's stream-token endpoint."""
        http = self.manager.mammotion_http
        if http is None:
            return Response(code=503, msg="Cloud session unavailable")
        await http.ensure_token_valid(caller="dual_camera_stream")
        login_info = http.login_info
        if login_info is None:
            return Response(code=401, msg="Not logged in")
        slots = vision_camera_slots(self.device_name)
        session = aiohttp_client.async_get_clientsession(self.hass)
        async with asyncio.timeout(30):
            async with session.post(
                f"{MAMMOTION_API_DOMAIN}/device-server/v1/stream/token",
                json={
                    "deviceId": self.device.iot_id,
                    "mode": 0,
                    "cameraStates": [
                        {"cameraState": int(slot < slots)} for slot in range(3)
                    ],
                },
                headers={
                    **http._headers,  # noqa: SLF001 - match PyMammotion request headers
                    "Authorization": f"Bearer {login_info.access_token}",
                    "Content-Type": "application/json",
                },
            ) as response:
                if response.status != 200:
                    return Response(code=response.status, msg="HTTP error")
                body = await response.json(content_type=None)
        if not isinstance(body, dict):
            return Response(code=502, msg="Invalid response")
        return response_factory(Response[StreamSubscriptionResponse], body)

    def set_stream_data(
        self, stream_data: Response[StreamSubscriptionResponse]
    ) -> None:
        """Set stream data."""
        self._stream_data = stream_data

    def get_stream_data(self) -> Response[StreamSubscriptionResponse]:
        """Return stream data."""
        return self._stream_data

    @property
    def dual_camera_stream_available(self) -> bool:
        """Whether the latest token request enabled every vision feed."""
        return self._dual_camera_stream_available

    @property
    def is_on_4g(self) -> bool:
        """Return True when the device's active network interface is 4G/cellular."""
        device = self.manager.get_device_by_name(self.device_name)
        try:
            return device.report_data.connect.used_net == NetUsedType.MNET
        except AttributeError:
            return False

    @callback
    def register_webrtc_session_control(
        self,
        control: WebRTCSessionControl | None,
        camera_key: str = "default",
    ) -> None:
        """Attach or detach one camera entity that owns this device's stream."""
        if control is None:
            self._webrtc_session_controls.pop(camera_key, None)
        else:
            self._webrtc_session_controls[camera_key] = control

    async def async_register_camera_session(
        self, camera_key: str, session_id: str
    ) -> None:
        """Track an active viewer for one of this mower's camera entities."""
        async with self._camera_session_lock:
            self._active_camera_sessions[camera_key] = session_id

    async def async_release_camera_session(
        self, camera_key: str, session_id: str
    ) -> None:
        """Stop the mower stream only after the last camera viewer has closed."""
        async with self._camera_session_lock:
            if self._active_camera_sessions.get(camera_key) != session_id:
                return
            del self._active_camera_sessions[camera_key]
            if not self._active_camera_sessions:
                await self.leave_webrtc_channel()

    @property
    def has_active_camera_sessions(self) -> bool:
        """Whether any of this mower's camera entities still has a viewer."""
        return bool(self._active_camera_sessions)

    async def join_webrtc_channel(self) -> None:
        """Start stream command."""
        await self.manager.get_stream_subscription(
            self.device.device_name, self.device.iot_id
        )

    async def leave_webrtc_channel(self) -> None:
        """End stream command.

        Runs the same teardown as the frontend closing its session: leave the
        Agora channel, then stop the mower's encoder.  Without a camera entity
        attached only the device-side half is possible.  Every viewer is
        forgotten, so the next offer mints a fresh token instead of reusing
        the one for the stream just stopped.
        """
        self._active_camera_sessions.clear()
        if self._webrtc_session_controls:
            await asyncio.gather(
                *(
                    control.async_teardown_stream(stop_device=False)
                    for control in self._webrtc_session_controls.values()
                )
            )
        await self.manager.stop_stream(self.device.device_name)

    async def set_scheduled_updates(self, enabled: bool) -> bool:
        """Enable or disable scheduled polling updates for this device.

        Only the poll loop is touched.  Transport state belongs to the Bluetooth
        and Cloud switches: detaching it here left every entity of the device
        unavailable — this switch included, so there was no way back short of
        reloading the entry — and re-attached on enable whatever the user had
        switched off (issue #889).
        """
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return False
        changed = device.enabled != enabled
        device.enabled = enabled
        if changed:
            # Once polling stops nothing else writes the snapshot, so a restart
            # would come back with updates on. The inbound handlers re-assert
            # ``True`` on every frame, hence the change check before the flush.
            self.async_save_data(device)
            await self.async_flush_saved_data()
            # ``data`` may be an earlier object than the library's current device.
            self.async_set_updated_data(self.get_coordinator_data(device))
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return False
        if not enabled:
            await handle.stop_polling()
            return changed
        self.update_failures = 0
        if not device.online:
            device.online = True
        try:
            # resume_polling, not restart_keep_alive: the latter honours the stop
            # this switch set, so on its own it would never bring the loops back.
            await handle.resume_polling()
        except TransportError as exc:
            # A BLE miss here (cooldown, stale cache) must not fail enabling
            # updates or escape into the state bus; polling carries on over MQTT.
            LOGGER.debug(
                "%s: keep-alive restart could not reach the device: %s",
                self.device_name,
                exc,
            )
        return changed

    async def _async_startup_reads(self) -> None:
        """Read back the settings this coordinator's entities show.

        Outbound and one-off, so it is kept apart from ``_async_setup``: the
        wiring there has to happen whatever the updates switch says, because
        ``_async_setup`` runs once per session, while these sends must not go
        out to a device whose polling the user turned off.
        """

    async def _async_ensure_startup_reads(self) -> None:
        """Run :meth:`_async_startup_reads` once, and only while enabled."""
        if self._startup_reads_done:
            return
        device = self.manager.get_device_by_name(self.device_name)
        if device is None or not device.enabled:
            return
        self._startup_reads_done = True
        await self._async_startup_reads()

    def is_online(self) -> bool:
        """Return True if the device currently has an active transport connection."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return False
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return bool(device.online)
        return handle.has_usable_transport

    @property
    def mqtt_transport_connected(self) -> bool:
        """Return True while either cloud MQTT transport of this mower is connected."""
        if handle := self.manager.mower(self.device_name):
            for t_type in (TransportType.CLOUD_ALIYUN, TransportType.CLOUD_MAMMOTION):
                if handle.is_transport_connected(t_type):
                    return True
        return False

    @property
    def mqtt_device_online(self) -> bool:
        """Return True while the cloud reports this mower online."""
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
        await self._store.async_set_transport_enabled(
            self.device_name, TRANSPORT_BLUETOOTH, enabled
        )
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return
        if not enabled:
            handle.set_prefer_ble(value=False)
            # Detach rather than disconnect: a merely disconnected BLE transport is
            # still selectable and gets reconnected once the cloud path is unusable.
            await handle.remove_transport(TransportType.BLE)
        else:
            handle.set_prefer_ble(value=True)
            await self._async_ensure_ble_client()

    async def async_set_cloud_enabled(self, enabled: bool) -> None:
        """Enable or disable Cloud transport."""
        self._cloud_enabled = enabled
        await self._store.async_set_transport_enabled(
            self.device_name, TRANSPORT_CLOUD, enabled
        )
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return
        # set_cloud_attached detaches this handle instead of disconnecting: the
        # cloud transports are one object per account, so disconnecting them for
        # one mower takes cloud down for every other mower on the account.
        with contextlib.suppress(TransportError):
            await self.manager.set_cloud_attached(self.device_name, attached=enabled)
        if enabled:
            await handle.restart_keep_alive()

    async def async_refresh_login(self, exc: Exception | None = None) -> None:
        """Refresh whichever credentials the failure actually implicates.

        LoginFailedError means an explicit login attempt was rejected, so the
        stored password is wrong — ask the user to reconfigure immediately.

        Otherwise route by what failed.  A SessionExpiredError names its transport,
        so refresh only that one; anything else goes through
        ``MammotionClient.refresh_login``, which itself routes by the transports the
        account actually has.

        Crucially, a ReLoginRequiredError does NOT always mean "make the user log in
        again".  pymammotion also raises it when one cloud transport's credentials
        are unrenewable while the account's HTTP login is still perfectly valid — it
        gives up on that transport alone and marks its mowers unavailable.  Forcing a
        reauth prompt there would cost the user their working credentials, and take
        down the account's *other* transport, to fix a fault confined to one.  Only
        ``MammotionClient.reauth_required`` — set when the HTTP refresh token itself
        is rejected — justifies ConfigEntryAuthFailed.
        """
        if not self.has_cloud_account:
            return
        if self.has_ble and self.manager.reauth_required is not None:
            # The account is dead but this mower still works over BLE: the reauth
            # flow was already started by the client's unrecoverable-auth callback,
            # and raising here would take the whole coordinator down.
            LOGGER.debug(
                "%s: cloud needs re-authentication; continuing over BLE",
                self.device_name,
            )
            return

        if isinstance(exc, LoginFailedError):
            raise ConfigEntryAuthFailed(
                f"Login failed for Mammotion account: {exc}"
            ) from exc

        try:
            if isinstance(exc, SessionExpiredError) and exc.transport_type in (
                TransportType.CLOUD_ALIYUN,
                TransportType.CLOUD_MAMMOTION,
            ):
                await self.manager.refresh_transport_credentials(exc.transport_type)
            else:
                await self.manager.refresh_login(self.account)
            self.store_cloud_credentials()
        except CloudSetupError as err:
            LOGGER.error("Aliyun cloud setup failed during re-login: %s", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cloud_setup_failed"
            ) from err
        except ReLoginRequiredError as err:
            if self.manager.reauth_required is None:
                # Transport-scoped: the account login is still good.  pymammotion has
                # already given up on the failing transport and signalled its mowers.
                LOGGER.warning(
                    "Mammotion account %s: a cloud transport is unavailable (%s); "
                    "account login is still valid, keeping stored credentials",
                    self.account,
                    err,
                )
                return
            raise ConfigEntryAuthFailed(
                f"Re-authentication required for Mammotion account: {err}"
            ) from err
        except (SessionExpiredError, UnauthorizedExceptionError) as err:
            # refresh_login re-raises its first failure, which can be one of these
            # rather than ReLoginRequiredError.  Left uncaught they land in HA's
            # generic handler, which reschedules the refresh — repeating a real
            # login-refresh attempt every poll interval indefinitely.
            if self.manager.reauth_required is not None:
                raise ConfigEntryAuthFailed(
                    f"Re-authentication required for Mammotion account: {err}"
                ) from err
            LOGGER.warning(
                "Mammotion account %s: credential refresh failed (%s); retrying on next update",
                self.account,
                err,
            )
        except Exception as err:
            if is_transient_network_error(err):
                LOGGER.debug(
                    "Transient network error during credential refresh: %s", err
                )
                return
            if self.manager.reauth_required is not None:
                raise ConfigEntryAuthFailed(
                    f"Re-authentication required for Mammotion account: {err}"
                ) from err
            raise HomeAssistantError(
                f"Credential refresh failed for Mammotion account: {err}"
            ) from err

    @staticmethod
    def _raise_if_user_waiting(priority: Priority, exc: Exception) -> None:
        """Surface a dropped command when a person is waiting on it.

        Background refreshes stay quiet — they retry on the next poll — but a user
        action that silently did nothing is the one outcome nobody can act on.
        """
        if priority.is_direct:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_failed"
            ) from exc

    async def async_send_and_wait(
        self,
        command: str,
        expected_field: str,
        priority: Priority = Priority.NORMAL,
        **kwargs: Any,
    ) -> None:
        """Send a command and wait for response with standard exception handling.

        Handles credential expiry, gateway/transport timeouts, and device-offline
        conditions uniformly.  Re-raises DeviceOfflineException after marking the
        device offline so callers can bail out of their update loops.

        Pass ``priority=Priority.USER`` for a command a person is waiting on: it
        spends past the library's self-imposed send quota, and a missing transport
        is surfaced instead of logged, since silence is the one outcome the user
        cannot act on.  See ``async_send_command`` for when that is appropriate.
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
                priority=priority,
                **kwargs,
            )
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
        except DeviceOfflineException:
            device = self.manager.get_device_by_name(self.device_name)
            if device is not None:
                self.device_offline(device)
        except (TooManyRequestsException, TransportRateLimitedError) as exc:
            # One message for both: TooManyRequestsException is the cloud's 429,
            # TransportRateLimitedError is the ban or quota it left behind.  A USER
            # command reaches the second one, and without this it surfaced as an
            # untranslated library traceback.
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="api_limit_exceeded"
            ) from exc
        except NoTransportAvailableError as exc:
            LOGGER.debug("No Transport: %s", exc)
            self._raise_if_user_waiting(priority, exc)
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

    async def _cloud_api_call[ResultT](
        self, coro: Coroutine[Any, Any, ResultT]
    ) -> ResultT | None:
        """Await a direct Mammotion HTTP call, mapping a dead login to ConfigEntryAuthFailed.

        The library fails these fast with ReLoginRequiredError (no network) once
        the account needs re-authentication; without this mapping the error lands
        in HA's generic handler and polling continues against a dead login.

        A mower that also has a BLE transport keeps working without the cloud, so
        for it the call degrades to ``None`` (the reauth flow is already running)
        instead of taking the coordinator down.
        """
        try:
            return await coro
        except ReLoginRequiredError as err:
            if self.has_ble:
                LOGGER.debug(
                    "%s: skipping cloud call, re-authentication pending: %s",
                    self.device_name,
                    err,
                )
                return None
            raise ConfigEntryAuthFailed(
                f"Re-authentication required for Mammotion account: {err}"
            ) from err

    def store_cloud_credentials(self) -> None:
        """Store cloud credentials in config entry.

        A rejected session is never persisted: ``to_cache()`` returns an empty
        dict once ``reauth_required`` is set, so this quietly skips.
        """
        if config_entry := self.config_entry:
            cache = self.manager.to_cache()
            if not cache:
                return
            self.hass.config_entries.async_update_entry(
                config_entry, data={**config_entry.data, **cache}
            )

    async def async_send_command(
        self, command: str, priority: Priority = Priority.NORMAL, **kwargs: Any
    ) -> bool | None:
        """Send command via MammotionClient command queue.

        ``priority=Priority.USER`` skips the queue and dispatches immediately, and
        spends past the library's self-imposed send quota (a cloud 429 still blocks).

        The choice is about *ordering*, not about the quota — that budget is spent
        by the library's own traffic (one ack per map frame, the MQTT poll loop,
        auto-fetch sagas) and by this coordinator's periodic refreshes, none of
        which is ever USER.  Use it where the command's *value decays*, i.e. where
        running late is worse than not running at all: manual movement,
        dock/undock, blades on/off, cancel.  Settings persist, so a blade height or
        light toggle queued behind a map sync still ends up correct and gains
        nothing from jumping it.
        """
        device = self.manager.get_device_by_name(self.device_name)
        if device is None or not self.is_online():
            return False

        try:
            await self.manager.send_command_with_args(
                self.device_name,
                command,
                prefer_ble=kwargs.pop("prefer_ble", self._bluetooth_enabled),
                skip_if_saga_active=False,
                priority=priority,
                **kwargs,
            )
        except FailedRequestException:
            self.update_failures += 1
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
        except GatewayTimeoutException as ex:
            LOGGER.error("Gateway timeout exception: %s", ex.iot_id)
            self.update_failures = 0
            return False
        except DeviceOfflineException:
            self.device_offline(device)
        except (TooManyRequestsException, TransportRateLimitedError) as exc:
            # One message for both: TooManyRequestsException is the cloud's 429,
            # TransportRateLimitedError is the ban or quota it left behind.  A USER
            # command reaches the second one, and without this it surfaced as an
            # untranslated library traceback.
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
        else:
            self.update_failures = 0
            return True
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
        except FailedRequestException:
            self.update_failures += 1
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
        except GatewayTimeoutException as ex:
            LOGGER.error("Gateway timeout exception: %s", ex.iot_id)
            self.update_failures = 0
            return False
        except (DeviceOfflineException, NoTransportAvailableError) as ex:
            LOGGER.error("Device offline: %s", ex.iot_id)
            self.device_offline(device)
            return False
        except (TooManyRequestsException, TransportRateLimitedError) as exc:
            # One message for both: TooManyRequestsException is the cloud's 429,
            # TransportRateLimitedError is the ban or quota it left behind.  A USER
            # command reaches the second one, and without this it surfaced as an
            # untranslated library traceback.
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="api_limit_exceeded"
            ) from exc
        except ReLoginRequiredError as err:
            raise ConfigEntryAuthFailed(
                f"Re-authentication required for Mammotion account: {err}"
            ) from err
        else:
            self.update_failures = 0
            return True
        return False

    async def async_send_bluetooth_command(
        self, key: str, priority: Priority = Priority.NORMAL, **kwargs: Any
    ) -> None:
        """Send command via BLE transport."""
        await self.async_send_command(key, priority=priority, prefer_ble=True, **kwargs)

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
        if http is not None and self.cloud_http_usable:
            await http.start_ota_upgrade(handle.iot_id, version)

    def _map_backup_http(self) -> MammotionHTTP:
        """Return the cloud HTTP client, or raise if map backups are unreachable."""
        http = self.manager.mammotion_http
        if http is None or not self.cloud_http_usable:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="map_backup_cloud_unavailable",
            )
        return http

    @staticmethod
    def _map_backup_data[T](response: Response[T]) -> T:
        """Unwrap a backup reply, raising with the server's reason on refusal.

        Mirrors ``BackupsViewModule``: the app ignores the envelope code and treats
        ``data: null`` (or a bare ``false``) as the refusal, reading the reason
        from the envelope, e.g. 60215 for a busy mower.
        """
        data = response.data
        if data is not None and data is not False:
            return data
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="map_backup_failed",
            translation_placeholders={"reason": f"{response.msg} ({response.code})"},
        )

    async def _async_map_backup_device_id(self) -> str:
        """Return the id the backup endpoints use for this device.

        Looked up from the cloud's own backup device list, as the app does, rather
        than assumed to be the iot id.
        """
        http = self._map_backup_http()
        devices: list[BackupMapItem] = self._map_backup_data(
            await http.get_map_backup_devices()
        )
        for device in devices:
            if device.device_name == self.device_name and device.device_id:
                return device.device_id
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="map_backup_device_not_found",
            translation_placeholders={"device_name": self.device_name},
        )

    async def async_list_map_backups(self) -> list[BackupMapItem]:
        """Return every map backup on the account."""
        http = self._map_backup_http()
        return self._map_backup_data(await http.get_map_backups())

    async def async_backup_map(
        self, name: str, backup_id: str | None = None
    ) -> BackupMapItem:
        """Upload this mower's map as a new backup, or over *backup_id*."""
        http = self._map_backup_http()
        device_id = await self._async_map_backup_device_id()
        if backup_id is None:
            response = await http.start_map_backup(
                device_id, name, MAP_BACKUP_CORRECTION_VALUE
            )
        else:
            response = await http.update_map_backup(
                backup_id, device_id, name, MAP_BACKUP_CORRECTION_VALUE
            )
        return self._map_backup_data(response)

    async def async_restore_map(self, backup_id: str) -> BackupMapResult:
        """Replace this mower's map with backup *backup_id*; the mower restarts."""
        http = self._map_backup_http()
        device_id = await self._async_map_backup_device_id()
        return self._map_backup_data(
            await http.restore_map_backup(device_id, backup_id)
        )

    async def async_get_map_backup_progress(
        self, backup_id: str, restore: bool
    ) -> BackupMapProgress:
        """Return how far a backup or restore job has got."""
        http = self._map_backup_http()
        progress_type = (
            BackupProgressType.RESTORE if restore else BackupProgressType.BACKUP
        )
        return self._map_backup_data(
            await http.get_map_backup_progress(backup_id, progress_type)
        )

    async def async_cancel_map_backup(self, backup_id: str, restore: bool) -> None:
        """Cancel a running backup or restore job."""
        http = self._map_backup_http()
        device_id = await self._async_map_backup_device_id()
        cancel = http.cancel_map_restore if restore else http.cancel_map_backup
        self._map_backup_data(await cancel(device_id, backup_id))

    async def async_delete_map_backup(self, backup_id: str) -> None:
        """Delete a stored map backup."""
        http = self._map_backup_http()
        self._map_backup_data(await http.delete_map_backup(backup_id))

    async def async_sync_maps(self) -> None:
        """Get map data from the device."""
        try:
            await self.manager.start_map_sync(self.device_name)
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
            # One retry after a successful refresh — never a recursion loop: each
            # iteration was a real refresh attempt with no delay between them.
            if self._can_retry_after_refresh():
                await self.manager.start_map_sync(self.device_name)

    async def async_sync_schedule(self) -> None:
        """Sync all scheduled mowing plans from the device via PlanFetchSaga."""
        try:
            await self.manager.start_plan_sync(self.device_name)
        except EXPIRED_CREDENTIAL_EXCEPTIONS as exc:
            self.update_failures += 1
            await self.async_refresh_login(exc)
            if self._can_retry_after_refresh():
                await self.manager.start_plan_sync(self.device_name)

    def _can_retry_after_refresh(self) -> bool:
        """Whether a post-refresh retry is worthwhile: account healthy and not failing repeatedly."""
        return self.manager.reauth_required is None and self.update_failures < 5

    async def async_fetch_audio_config(self) -> None:
        """Read current audio config (volume, language, gender) from device."""
        await self.async_send_and_wait("get_car_audio_cfg", "audio_cfg")

    async def async_set_voice_volume(self, volume: float) -> None:
        """Set robot voice volume (0–100)."""
        await self.async_send_and_wait(
            "set_car_volume",
            "set_audio",
            priority=Priority.USER,
            volume=int(volume),
        )

    async def async_set_voice_on_off(self, on: bool) -> None:
        """Turn robot voice on (restores 50%) or off (sets volume to 0)."""
        await self.async_send_and_wait(
            "set_car_volume",
            "set_audio",
            priority=Priority.USER,
            volume=50 if on else 0,
        )

    async def async_set_voice_gender(self, sex: str) -> None:
        """Set robot voice gender (MAN or WOMAN)."""
        await self.async_send_and_wait(
            "set_car_volume_sex",
            "set_audio",
            priority=Priority.USER,
            sex=MulSex[sex],
        )

    async def async_start_stop_blades(
        self, start_stop: bool, blade_height: int = 60
    ) -> None:
        """Start stop blades."""
        if DeviceType.is_luba1(self.device_name):
            if start_stop:
                await self.async_send_and_wait(
                    "set_blade_control",
                    "toapp_knife_status_change",
                    priority=Priority.USER,
                    on_off=1,
                )
            else:
                await self.async_send_and_wait(
                    "set_blade_control",
                    "toapp_knife_status_change",
                    priority=Priority.USER,
                    on_off=0,
                )
        elif start_stop:
            if DeviceType.is_yuka(self.device_name) or DeviceType.is_yuka_mini(
                self.device_name
            ):
                blade_height = 0

            await self.async_send_command(
                "operate_on_device",
                priority=Priority.USER,
                main_ctrl=1,
                cut_knife_ctrl=1,
                cut_knife_height=blade_height,
                max_run_speed=1.2,
            )
        else:
            await self.async_send_command(
                "operate_on_device",
                priority=Priority.USER,
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
            await self.async_send_command(
                "job_do_not_disturb_del",
                priority=Priority.USER,
            )
            return

        def _to_minutes(hhmm: str) -> str:
            h, m = hhmm.split(":")
            return str(int(h) * 60 + int(m))

        await self.async_send_command(
            "job_do_not_disturb",
            priority=Priority.USER,
            unable_end_time=_to_minutes(end_time),
            unable_start_time=_to_minutes(start_time),
        )

    async def async_reset_blade_time(self) -> None:
        """Reset blade used time."""
        await self.async_send_and_wait(
            "reset_blade_time",
            "todev_reset_blade_used_time_status",
            priority=Priority.USER,
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
            priority=Priority.USER,
            rw_id=3,
            context=int(on_off),
            rw=1,
        )

    async def async_read_rain_detection(self) -> None:
        """Read current rain detection state from device."""
        await self.async_send_and_wait(
            # context=1 on the read, as the app sends it (DrawerSettingsViewModel
            # .readDeviceState: allpowerfullRW(3, 1, 0)).  It is per-id, not a blanket
            # convention — ids 20-23 really do read with context=0.
            "read_write_device",
            self._rw_expected_field(3),
            rw_id=3,
            context=1,
            rw=0,
        )

    async def async_read_battery_info(self) -> None:
        """Read the battery charge limit and off-peak charging settings."""
        await self.async_send_and_wait("query_battery_info", "bms_ctrl_info_msg")

    async def async_set_charge_limit(self, charge_limit: int) -> None:
        """Set a fixed battery charge limit, which turns smart charging off."""
        await self._async_set_battery_info(False, charge_limit)

    async def async_set_smart_charge(self, smart_charge: bool) -> None:
        """Turn smart charging on or off.

        Switching it off keeps the limit the device last reported, which is 100
        while smart charging is active — the same value the app sends.
        """
        current = self.data.mower_state.charge_settings
        await self._async_set_battery_info(smart_charge, current.charge_limit or 100)

    async def _async_set_battery_info(
        self, smart_charge: bool, charge_limit: int
    ) -> None:
        """Send bms_ctrl_info_msg, resending the off-peak window it would otherwise reset."""
        current = self.data.mower_state.charge_settings
        await self.async_send_and_wait(
            "set_battery_info",
            "bms_ctrl_info_msg",
            smart_charge=smart_charge,
            charge_limit=charge_limit,
            peak_valley_charge=current.peak_valley_charge,
            valley_charge_start_time=current.valley_charge_start_time,
            valley_charge_end_time=current.valley_charge_end_time,
        )

    async def async_set_sidelight(self, on_off: int) -> None:
        """Set Sidelight."""
        await self.async_send_and_wait(
            "read_and_set_sidelight",
            "todev_time_ctrl_light",
            priority=Priority.USER,
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
            "set_car_manual_light",
            "set_lamp_rsp",
            priority=Priority.USER,
            manual_ctrl=manual_ctrl,
        )

    async def async_read_manual_light(self) -> None:
        """Read current manual light state from device."""
        await self.async_send_and_wait("get_car_light", "get_lamp_rsp", ids=1126)

    async def async_set_night_light(self, night_light: bool) -> None:
        """Set night light."""
        await self.async_send_and_wait(
            "set_car_light",
            "set_lamp_rsp",
            priority=Priority.USER,
            on_off=night_light,
        )

    async def async_read_night_light(self) -> None:
        """Read current night light state from device."""
        await self.async_send_and_wait("get_car_light", "get_lamp_rsp", ids=1123)

    async def async_set_traversal_mode(self, context: int) -> None:
        """Set traversal mode."""
        await self.async_send_and_wait(
            "traverse_mode",
            self._rw_expected_field(7),
            priority=Priority.USER,
            context=context,
        )

    async def async_read_traversal_mode(self) -> None:
        """Read current traversal mode from device."""
        await self.async_send_and_wait(
            # allpowerfullRW(7, 1, 0) in the app
            "read_write_device",
            self._rw_expected_field(7),
            rw_id=7,
            context=1,
            rw=0,
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
            priority=Priority.USER,
            rw_id=13,
            context=status,
            rw=1,
        )
        await self.async_send_and_wait(
            "read_write_device",
            self._rw_expected_field(12),
            priority=Priority.USER,
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
            "turning_mode",
            self._rw_expected_field(6),
            priority=Priority.USER,
            context=context,
        )

    async def async_read_turning_mode(self) -> None:
        """Read current turning mode from device."""
        await self.async_send_and_wait(
            # allpowerfullRW(6, 1, 0) in the app
            "read_write_device",
            self._rw_expected_field(6),
            rw_id=6,
            context=1,
            rw=0,
        )

    async def async_blade_height(self, height: int) -> int:
        """Set blade height."""
        await self.async_send_and_wait(
            "set_blade_height",
            "toapp_knife_status_change",
            priority=Priority.USER,
            height=height,
        )
        return height

    async def async_set_cutter_speed(self, mode: int) -> None:
        """Set cutter speed."""
        await self.async_send_and_wait(
            "set_cutter_mode",
            "cutter_mode_ctrl_by_hand",
            priority=Priority.USER,
            cutter_mode=mode,
        )

    async def async_read_cutter_mode(self) -> None:
        """Query the current cutter mode and live RPM from the device."""
        await self.async_send_and_wait("get_cutter_mode", "current_cutter_mode")

    async def async_reset_blade_warning_time(self) -> None:
        """Reset blade used time to zero."""
        await self.async_send_and_wait(
            "reset_blade_time",
            "todev_reset_blade_used_time_status",
            priority=Priority.USER,
        )

    async def async_set_blade_warning_time(self, hours: int) -> None:
        """Set the blade warning time in hours."""
        await self.async_send_command(
            "set_blade_warning_time",
            priority=Priority.USER,
            hours=hours,
        )

    async def async_set_speed(self, speed: float) -> None:
        """Set working speed."""
        await self.async_send_and_wait(
            "set_speed",
            "bidire_speed_read_set",
            priority=Priority.USER,
            speed=speed,
        )

    async def async_leave_dock(self) -> None:
        """Leave dock."""
        await self.send_command_and_update(
            "leave_dock", "todev_taskctrl_ack", priority=Priority.USER
        )

    async def async_start_no_area_work(self) -> None:
        """Start a map-free mow from where the mower stands (the app's DropMow).

        No map and no boundary: the mower works from the direction it is
        currently facing.  The app only offers this on the X5 models and only
        while the mower is idle, which is what ``supports_no_area_work`` and the
        button's availability mirror.
        """
        await self.send_command_and_update(
            "start_no_area_work", "todev_taskctrl_ack", priority=Priority.USER
        )

    async def async_cancel_task(self) -> None:
        """Cancel task."""
        await self.send_command_and_update(
            "cancel_job", "todev_taskctrl_ack", priority=Priority.USER
        )

    @property
    def grass_collector_installed(self) -> bool:
        """Return True while the mower reports a grass collector fitted."""
        device = cast(MowingDevice | None, self.data)
        return device is not None and device.report_data.dev.collector_installed

    @property
    def grass_collection_state(self) -> CollectorState:
        """Return the sweep state, or IDLE before the first report lands."""
        device = cast(MowingDevice | None, self.data)
        if device is None:
            return CollectorState.IDLE
        return device.report_data.dev.collector_state

    @property
    def grass_dump_state(self) -> DumpState:
        """Return the bin-tipping state, or LOWERED before the first report lands."""
        device = cast(MowingDevice | None, self.data)
        if device is None:
            return DumpState.LOWERED
        return device.report_data.dev.dump_state

    async def async_set_grass_collection(self, start: bool) -> None:
        """Start or stop manual grass collection (sweeping)."""
        await self.async_send_command(
            "manual_grass_collection",
            priority=Priority.USER,
            collect_ctrl=int(start),
        )

    async def async_set_grass_dump(self, start: bool) -> None:
        """Raise the collector bin to pour the clippings out, or lower it again."""
        await self.async_send_command(
            "manual_pour_grass",
            priority=Priority.USER,
            unload_ctrl=int(start),
        )

    async def async_enter_dump_point_setup(self) -> None:
        """Put the mower into grass-collection point setup mode."""
        await self.async_send_command("enter_dumping_status", priority=Priority.USER)

    async def async_add_dump_point(self) -> None:
        """Record a grass-collection point at the mower's current position."""
        await self.async_send_command("add_dump_point", priority=Priority.USER)

    async def async_revoke_dump_point(self) -> None:
        """Undo the last recorded grass-collection point."""
        await self.async_send_command("revoke_dump_point", priority=Priority.USER)

    async def async_exit_dump_point_setup(self) -> None:
        """Save the recorded grass-collection points and leave setup mode."""
        await self.async_send_command("exit_dumping_status", priority=Priority.USER)

    async def async_finish_outside_dump_point(self) -> None:
        """Finish a collection point recorded outside the mowing area.

        The app sends this instead of ``exit_dumping_status`` when the point was
        added while the mower sat off-map
        (``PlanMapLandFragment.onClickCompleteDump``).
        """
        await self.async_send_command("out_drop_dumping_add", priority=Priority.USER)

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
            "move_forward",
            priority=Priority.USER,
            prefer_ble=not use_wifi,
            linear=speed,
        )

    async def async_move_left(self, speed: float, use_wifi: bool = False) -> None:
        """Move left. Prefer BLE unless use_wifi=True."""
        if not use_wifi:
            await self._async_ensure_ble_client()
        await self.async_send_command(
            "move_left",
            priority=Priority.USER,
            prefer_ble=not use_wifi,
            angular=speed,
        )

    async def async_move_right(self, speed: float, use_wifi: bool = False) -> None:
        """Move right. Prefer BLE unless use_wifi=True."""
        if not use_wifi:
            await self._async_ensure_ble_client()
        await self.async_send_command(
            "move_right",
            priority=Priority.USER,
            prefer_ble=not use_wifi,
            angular=speed,
        )

    async def async_move_back(self, speed: float, use_wifi: bool = False) -> None:
        """Move back. Prefer BLE unless use_wifi=True."""
        if not use_wifi:
            await self._async_ensure_ble_client()
        await self.async_send_command(
            "move_back",
            priority=Priority.USER,
            prefer_ble=not use_wifi,
            linear=speed,
        )

    async def async_rtk_dock_location(self) -> None:
        """RTK and dock location."""
        await self.async_send_and_wait(
            "read_write_device",
            "bidire_comm_cmd",
            priority=Priority.USER,
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
            priority=Priority.USER,
            device_id=self.device.iot_id,
            hash_id=hash_id,
            name=name,
        )

    async def async_relocate_charging_station(self) -> None:
        """Reset charging station."""
        await self.async_send_command("delete_charge_point", priority=Priority.USER)
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
        self,
        command_str: str,
        response: str | None = None,
        priority: Priority = Priority.NORMAL,
        **kwargs: Any,
    ) -> None:
        """Send command and update."""
        if response is not None:
            await self.async_send_and_wait(
                command_str, response, priority=priority, **kwargs
            )
        else:
            await self.async_send_command(command_str, priority=priority, **kwargs)
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

        ``send_svg`` splits the message into frames and waits for a per-frame device
        ACK; this waits for the device-assigned ``data_hash`` that comes back with the
        last one, for use in subsequent UPDATE or DELETE operations.

        Args:
            svg_message: Fully-populated message from
                         :func:`~pymammotion.utility.svg.build_svg_for_area` or
                         :func:`~pymammotion.utility.svg.build_svg_update`.

        Returns:
            Device-assigned ``data_hash``, or ``None`` when the transfer was not
            confirmed within ``SVG_SEND_TIMEOUT`` — the transfer itself may still be
            running, only the hash is given up.

        """

        # Hand over the whole message: send_svg chunks internally.  Pre-chunking here
        # and passing the list was Mammotion-HA#868 — it chunked twice and raised
        # "'list' object has no attribute 'svg_message'", so nothing ever transferred.
        loop = asyncio.get_running_loop()
        transferred: asyncio.Future[int | None] = loop.create_future()

        async def _on_complete(device_hash: int | None) -> None:
            if not transferred.done():
                transferred.set_result(device_hash)

        await self.manager.send_svg(
            self.device_name, svg_message, on_complete=_on_complete
        )

        # send_svg returns once the saga is *queued*; the hash arrives on the callback
        # when it completes.  on_complete does not fire if the saga fails, so the wait
        # is bounded — None here means "not confirmed within the window", not "failed".
        try:
            async with asyncio.timeout(SVG_SEND_TIMEOUT.total_seconds()):
                return await transferred
        except TimeoutError:
            LOGGER.warning(
                "SVG transfer for %s was not confirmed within %ss",
                self.device_name,
                SVG_SEND_TIMEOUT.total_seconds(),
            )
            return None

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
            auto_change_direction=operation_settings.auto_change_direction,
        )

        if DeviceType.is_luba1(self.device_name):
            route_information.toward_mode = 0
            route_information.toward_included_angle = 0
        firmware = getattr(
            getattr(self.data, "device_firmwares", None), "device_version", ""
        )
        if not DeviceType.supports_auto_change_direction(
            self.device_name, firmware or ""
        ):
            # The app gates this row on a capability list and firmware; match it.
            route_information.auto_change_direction = 0
        return route_information

    async def async_plan_route(
        self,
        operation_settings: OperationSettings,
        priority: Priority = Priority.NORMAL,
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
            priority=priority,
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
        self,
        operation_settings: OperationSettings,
        priority: Priority = Priority.NORMAL,
    ) -> bool | None:
        """Modify plan mow.

        Reached both from a user pressing start (``Priority.USER``) and from
        ``async_modify_plan_if_mowing`` re-planning on its own, so the caller
        decides — this must not be marked USER wholesale.
        """

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
            "modify_route_information",
            priority=priority,
            generate_route_information=route_information,
        )

    async def start_task(self, plan_id: str) -> None:
        """Start task."""
        await self.async_send_and_wait(
            "single_schedule",
            "todev_planjob_set",
            priority=Priority.USER,
            plan_id=plan_id,
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
        await self.async_send_command(
            "create_plan",
            priority=Priority.USER,
            plan=plan_with_id,
        )

    async def async_edit_mower_task(self, plan: Plan) -> None:
        """Edit an existing mower schedule (``sub_cmd=4``)."""
        await self.async_send_command("edit_plan", priority=Priority.USER, plan=plan)

    async def async_rename_mower_task(self, plan_id: str, new_name: str) -> None:
        """Rename the mower schedule identified by ``plan_id`` to ``new_name``."""
        plan = self._lookup_mower_plan(plan_id)
        await self.async_send_command(
            "rename_plan",
            priority=Priority.USER,
            plan=plan,
            new_name=new_name,
        )

    async def async_set_mower_task_enabled(self, plan_id: str, enabled: bool) -> None:
        """Flip the enable flag (``reserved[2]``) on an existing mower schedule.

        The existing plan is round-tripped verbatim so the other reserved
        bytes (knife height, edge mode, …) are preserved.
        """
        plan = self._lookup_mower_plan(plan_id)
        await self.async_send_command(
            "enable_plan",
            priority=Priority.USER,
            plan=plan,
            enabled=enabled,
        )

    async def async_delete_mower_task(self, plan_id: str) -> None:
        """Delete the mower schedule identified by ``plan_id`` (``sub_cmd=3``)."""
        await self.async_send_command(
            "delete_plan_by_id",
            priority=Priority.USER,
            plan_id=plan_id,
        )

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
            priority=Priority.USER,
            plan=plan,
            new_name=resolved_name,
            new_plan_id=new_mower_plan_id(),
        )

    async def async_refresh_mower_tasks(self) -> None:
        """Re-fetch the mower schedule list via :class:`PlanFetchSaga`."""
        await self.manager.start_plan_sync(self.device_name)

    async def async_restart_mower(self) -> None:
        """Restart mower."""
        await self.async_send_command("remote_restart", priority=Priority.USER)

    def clear_update_failures(self) -> None:
        """Clear update failures and reconnect transports if needed."""
        self.update_failures = 0

    @property
    def operation_settings(self) -> OperationSettings:
        """Return operation settings for planning."""
        return self._operation_settings

    def _is_route_job_running(self) -> bool:
        """Return True while a route mow is in progress and not yet complete.

        The mower's current breakpoint (``report_data.work.bp_hash``) is one of the
        active job's zones (``work.zone_hashs``) and its progress (``area >> 16``)
        is not 100. This detects a mow underway; it does not distinguish a scheduled
        task from a manually started one.
        """
        _mdata = cast(MowingDevice, self.data)
        return (
            int(_mdata.report_data.work.bp_hash) in _mdata.work.zone_hashs
            and (_mdata.report_data.work.area >> 16) != 100
        )

    def _seed_operation_settings_from_running_job(self) -> None:
        """Copy the running job's parameters into operation settings.

        The app seeds its in-job WorkingOptionView from the active route
        (``queryGenerateRouteInformation``) and re-sends the whole set with only
        the edited field changed, so the running job's real speed, spacing,
        detection and route settings must survive a mid-job tweak rather than
        being reset to the planning defaults.
        """
        work = cast(MowingDevice, self.data).work
        settings = self._operation_settings
        settings.areas = list(dict.fromkeys(work.zone_hashs))
        settings.toward = work.toward
        settings.toward_mode = work.toward_mode
        settings.toward_included_angle = work.toward_included_angle
        settings.mowing_laps = work.edge_mode
        settings.job_mode = work.job_mode
        settings.job_id = work.job_id
        settings.job_version = work.job_ver
        settings.speed = work.speed
        settings.channel_width = work.channel_width
        settings.ultra_wave = work.ultra_wave
        settings.channel_mode = work.channel_mode
        settings.blade_height = work.knife_height
        settings.auto_change_direction = work.auto_change_direction
        # create_path_order rebuilds the reserved buffer from these three, so
        # without seeding them a mid-job tweak ships the planning values —
        # notably start_progress, which would otherwise go out as whatever the
        # slider happens to hold rather than where the mower actually is.
        if work.reserved:
            order = GenerateRouteInformation.decode_path_order(
                _reserved_without_echo(work.reserved)
            )
            settings.border_mode = order.edge_mode
            settings.obstacle_laps = order.obstacle_laps
            settings.start_progress = order.start_progress

    async def async_modify_plan_if_mowing(self) -> None:
        """Re-plan the current mow route if the device is actively mowing."""
        if self._is_route_job_running():
            await self.async_modify_plan_route(self.operation_settings)

    async def async_change_blade_height_if_working(self) -> None:
        """Apply a blade-height change to a running job the way the app does.

        Mirrors ``HomeMapFragment`` WorkingOptionView.onConfirm: Luba 2 and
        newer re-issue the running route (subCmd 3) so the new height binds to
        the active job, while the original Luba 1 nudges the blade motor
        directly (``setKnifeHight``). An idle change is baked into the plan when
        the next job starts, so nothing is sent to the device in that case.

        For the route re-issue the app changes only the height on the running
        job's full route, so seed every other parameter from the active job
        before applying the new height — otherwise speed, spacing and detection
        would be clobbered with defaults.
        """
        if not self._is_route_job_running():
            return
        new_height = self._operation_settings.blade_height
        if not DeviceType.is_luba_pro(self.device_name):
            await self.async_blade_height(new_height)
            return
        self._seed_operation_settings_from_running_job()
        self._operation_settings.blade_height = new_height
        await self.async_modify_plan_route(self._operation_settings)

    async def _apply_route_field_if_working(self, field: str) -> None:
        """Re-issue the running job's route with a single route field changed.

        The value is read off ``operation_settings`` first, because an entity
        has already written it there.
        """
        await self.async_modify_running_job(
            **{field: getattr(self._operation_settings, field)}
        )

    async def async_modify_running_job(self, **changes: Any) -> bool:
        """Change one or more route settings on the job already running.

        Mirrors the app's in-job editor (``WorkingOptionView``): it seeds from
        the active route and re-sends the whole parameter set with only the
        edited fields changed, so everything else about the running job
        survives.  Returns False when there is nothing to change it on.

        Only fields ``async_modify_plan_route`` does not reseed can be changed
        this way; it forces the job's own identity and geometry (areas, toward,
        toward_mode, toward_included_angle, mowing_laps, job_mode) back from
        the device, which is what keeps a tweak from re-planning the job.

        The original Luba 1's in-job editor only offers blade height, which it
        sends as a direct command instead, so nothing is re-issued there.
        """
        if not self._is_route_job_running():
            return False
        if not DeviceType.is_luba_pro(self.device_name):
            return False
        self._seed_operation_settings_from_running_job()
        for field, value in changes.items():
            if value is not None:
                setattr(self._operation_settings, field, value)
        await self.async_modify_plan_route(self._operation_settings)
        return True

    async def async_change_speed_if_working(self) -> None:
        """Apply a mid-job task-speed change, preserving the running job's route."""
        await self._apply_route_field_if_working("speed")

    async def async_change_bypass_if_working(self) -> None:
        """Apply a mid-job obstacle-detection change, preserving the running job's route."""
        await self._apply_route_field_if_working("ultra_wave")

    async def async_change_progress_if_working(self) -> None:
        """Apply a mid-job progress change, preserving the running job's route."""
        await self._apply_route_field_if_working("start_progress")

    async def async_restore_data(self) -> None:
        """Restore saved data."""
        restored_data: Mapping[str, Any] | None = await self._store.async_device_data(
            self.device_name
        )

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

    @callback
    def async_save_data(self, data: MowingDevice | PoolCleanerDevice) -> None:
        """Queue device state for persistence.

        Returns immediately: the state is kept in memory and written at most
        once per ``SAVE_DELAY``.  Callers on the poll path must not wait for the
        disk.
        """
        self._store.async_update_device_data(
            self.device_name, cast(dict[str, Any], data.to_dict())
        )

    async def async_flush_saved_data(self) -> None:
        """Write any queued device state to disk immediately."""
        await self._store.async_flush()

    async def remove_saved_data(self) -> None:
        """Remove saved coordinator data from persistent storage."""
        await self._store.async_remove_device(self.device_name)

    async def _async_update_data(self) -> DataT | None:
        """Update data from the device."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return None

        if not device.enabled:
            return self.get_coordinator_data(device)

        # Reads skipped at setup because updates were off: the switch only ever
        # reaches the report coordinator, so its siblings pick them up here on
        # their first enabled refresh rather than waiting for a reload.
        await self._async_ensure_startup_reads()

        handle = self.manager.mower(self.device_name)

        if not self.is_online():
            return self.get_coordinator_data(device)

        # Update BLE device address from HA bluetooth scanner if available
        if (
            self._bluetooth_enabled
            and device.mower_state.ble_mac != ""
            and handle is not None
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

    @callback
    def subscribe_notification(
        self, handler: Callable[[DeviceNotification], Awaitable[None]]
    ) -> CALLBACK_TYPE:
        """Subscribe to the device's thing/event notifications; returns an unsubscribe callable."""
        handle = self.manager.mower(self.device_name)
        if handle is None:
            return lambda: None

        async def _on_notification(notification: DeviceNotification) -> None:
            if not self.hass.is_stopping:
                await handler(notification)

        return handle.subscribe_notification(_on_notification).cancel

    async def async_shutdown(self) -> None:
        """Flush queued state, cancel RAII subscriptions and shut down the coordinator."""
        await self.async_flush_saved_data()
        for sub in self._subscriptions:
            sub.cancel()
        self._subscriptions.clear()
        await super().async_shutdown()

    async def _on_state_changed(self, snapshot: DeviceSnapshot) -> None:
        """Push updated device data to HA."""
        self.device.online = True
        LOGGER.debug(
            "%s: state-changed push, snapshot.raw online=%s",
            self.device_name,
            getattr(snapshot.raw, "online", None),
        )
        self.async_set_updated_data(snapshot.raw)
        await self._async_device_reported_in()

    async def _async_device_reported_in(self) -> None:
        """React to fresh contact from the device."""

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

        _mower_data = cast(MowingDevice, self.data)
        if area_hash not in _mower_data.map.area:
            return "path"

        # Prefer the user's HA-level entity name over the device-assigned name.
        entity_reg = er.async_get(self.hass)
        unique_id = f"{self.unique_name}_{area_hash}"
        entity_id = entity_reg.async_get_entity_id("switch", DOMAIN, unique_id)
        if entity_id and (entry := entity_reg.async_get(entity_id)) and entry.name:
            return entry.name

        for area in _mower_data.map.computed_areas:
            if area.hash == area_hash:
                return area.name

        return f"area {area_hash}"

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

        mower_data = self.data
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
        self.hass.create_task(self._async_push_advertisement(handle))

    async def _async_push_advertisement(self, handle: DeviceHandle) -> None:
        """Hand the freshest advertisement to the BLE transport (creating it if needed) and connect."""
        if self.service_info is None:
            return
        await self.manager.update_ble_device(
            self.device_name, self.service_info.device, self.service_info.rssi
        )
        ble = handle.get_transport(TransportType.BLE)
        if (
            ble is not None
            and not ble.is_connected
            and self.data is not None
            and self.data.enabled
        ):
            with contextlib.suppress(TransportError):
                await ble.connect()

    async def async_set_bluetooth_enabled(self, enabled: bool) -> None:
        """Enable or disable Bluetooth, reconnecting if re-enabled."""
        await super().async_set_bluetooth_enabled(enabled)
        if enabled:
            self._add_ble_device()

    @callback
    def _async_start(self) -> None:
        """Subscribe to this mower's advertisements once its MAC is known.

        Idempotent, and called from both setup and the refresh: a mower first
        seen after startup has no MAC at setup time.
        """
        if self._on_stop or self.data.mower_state.ble_mac == "":
            return
        self._on_stop.append(
            async_register_callback(
                self.hass,
                self._async_handle_bluetooth_event,
                # Home Assistant matches the address verbatim, and advertisements are upper-case.
                BluetoothCallbackMatcher(
                    address=self.data.mower_state.ble_mac.upper(), connectable=True
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

    async def async_shutdown(self) -> None:
        """Drop the advertisement subscription along with the rest."""
        self._async_stop()
        await super().async_shutdown()

    async def _async_reconnect_ble(self) -> None:
        """Reconnect a usable-but-idle BLE link.

        Runs before the refresh delegates to the base, which returns early
        while ``is_online()`` is False.  On a mower whose only transport is
        this one, BLE being down *is* being offline, so leaving the retry
        behind that early return meant it ran only while already connected
        and the link never came back by itself.
        """
        if not self._bluetooth_enabled or not self.data.enabled:
            return
        handle = self.manager.mower(self.device_name)
        if handle is None or not handle.prefer_ble:
            return
        ble = handle.get_transport(TransportType.BLE)
        if ble is None or ble.is_connected or not ble.is_usable:
            return
        try:
            await ble.connect()
        except BLEUnavailableError as exc:
            LOGGER.debug(
                "BLE unavailable for %s during update — continuing via cloud: %s",
                self.device_name,
                exc,
            )

    def get_coordinator_data(self, device: MowingDevice) -> MowingDevice:
        """Get coordinator data."""
        return device

    async def _async_update_data(self) -> MowingDevice:
        """Get data from the device."""
        self._async_start()
        await self._async_reconnect_ble()

        if data := await super()._async_update_data():
            return data

        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            LOGGER.debug("device not found")
            return self.data

        LOGGER.debug("Updated Mammotion device %s", self.device_name)
        self.update_failures = 0
        self.async_save_data(device)

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
        self._async_start()

        # With the updates switch off this coordinator sends nothing until it goes
        # back on.  Only the outbound reads are skipped — the sys_status watch is
        # wired below either way, because _async_setup runs once per session and
        # skipping it here would strand the watch until a config-entry reload.
        if not self.data.enabled:
            if handle := self.manager.mower(self.device_name):
                await handle.stop_polling()
        await self._async_ensure_startup_reads()

        # Watch sys_status changes so we can refresh the full status when the
        # device transitions states.  Skipped when the BLE polling loop is
        # already feeding a continuous count=0 stream — the stream is fresher
        # than any count=1 poll we could fire.
        if (handle := self.manager.mower(self.device_name)) is not None:
            handle.watch_field(
                lambda s: s.raw.report_data.dev.sys_status,
                self._on_sys_status_changed_refresh,
            )

    async def set_scheduled_updates(self, enabled: bool) -> bool:
        """Run the startup reads that setup skipped when updates were off.

        Backgrounded: the reads carry SETUP_COMMAND_BUDGET, so awaiting them here
        would hold ``switch.turn_on`` open for up to a minute against a mower that
        is not answering.
        """
        changed = await super().set_scheduled_updates(enabled)
        if changed and enabled and (entry := self.config_entry) is not None:
            entry.async_create_background_task(
                self.hass,
                self._async_ensure_startup_reads(),
                f"{self.device_name} updates-on reads",
            )
        return changed

    async def _async_startup_reads(self) -> None:
        """Read back the settings the entities show, under one time budget."""
        # Common commands for all device types
        commands = [
            ("send_todev_ble_sync", {"sync_type": 3}),
            ("async_read_rain_detection", {}),
            ("async_read_sidelight", {}),
            ("async_read_turning_mode", {}),
            ("async_read_traversal_mode", {}),
        ]

        # Add device-specific commands.  Two separate capabilities, gated the way the
        # app gates them: the lights on isSupportFillLight (night light excluded on
        # Yuka MV) and the cutter mode on isSupportBladeSpeed.
        if DeviceType.is_support_fill_light(self.device_name):
            commands.append(("async_read_manual_light", {}))
            if not DeviceType.value_of_str(self.device_name).is_yuka_mv():
                commands.append(("async_read_night_light", {}))
        if DeviceType.is_support_blade_speed(self.device_name):
            commands.append(("async_read_cutter_mode", {}))

        firmware = getattr(
            getattr(self.data, "device_firmwares", None), "device_version", ""
        )
        if DeviceType.is_luba_pro(self.device_name):
            commands.append(("async_fetch_audio_config", {}))
            if DeviceType.supports_wildlife_safety(self.device_name, firmware or ""):
                commands.append(("async_read_wildlife_safety", {}))
        if DeviceType.supports_charge_limit(self.device_name, firmware or ""):
            commands.append(("async_read_battery_info", {}))

        # Final command for all devices
        commands.append(("async_request_report_snapshot", {}))

        # Execute all commands with unified exception handling, under one budget so an
        # unresponsive mower cannot hold up the config entry (see SETUP_COMMAND_BUDGET).
        pending = [name for name, _ in commands]
        try:
            async with asyncio.timeout(SETUP_COMMAND_BUDGET.total_seconds()):
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
                        LOGGER.debug(
                            "Command %s failed with exception: %s", command_name, exc
                        )
                    # Not in a `finally`: when the budget expires mid-command this line
                    # is skipped, so the command that actually stalled stays in the list.
                    pending.remove(command_name)
        except TimeoutError:
            # Ours, not HA's: setup continues, the unread settings show their defaults
            # until a later refresh picks them up.
            LOGGER.warning(
                "Setup reads for %s exceeded %ss; continuing without: %s",
                self.device_name,
                SETUP_COMMAND_BUDGET.total_seconds(),
                ", ".join(pending),
            )

    async def _on_sys_status_changed_refresh(self, sys_status: int) -> None:
        """Trigger a one-shot count=1 poll on sys_status transitions when not streaming."""
        if not self.data.enabled:
            return
        try:
            await self.async_request_report_snapshot()
        except DeviceOfflineException, NoTransportAvailableError:
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
            with contextlib.suppress(DeviceOfflineException, GatewayTimeoutException):
                await self.async_send_command("get_maintenance")

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

        await self._async_ensure_startup_reads()

    async def _async_startup_reads(self) -> None:
        """Fetch the maintenance counters and the do-not-disturb window."""
        try:
            await self.async_send_command("get_maintenance")
            await self.async_send_and_wait(
                "read_job_do_not_disturb", "todev_unable_time_set"
            )
        except DeviceOfflineException, GatewayTimeoutException:
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

        self._firmware_check_attempted: datetime.datetime | None = None
        #: (product_key, int_mod) -> the firmware we asked at and got nothing for.
        self._capability_miss: dict[tuple[str, str], str] = {}
        #: (product_key, int_mod) -> the parsed schema, so a lookup is cheap.
        self._capability_cache: dict[tuple[str, str], dict[str, ProductParam]] = {}

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

        if handle is not None and self.cloud_http_usable:
            http = self.manager.mammotion_http
            if http is not None:
                ota_info = await self._cloud_api_call(
                    http.get_device_ota_firmware([handle.iot_id])
                )
                LOGGER.debug("OTA info: %s", ota_info.data if ota_info else None)
                if ota_info is not None:
                    await self._store.async_set_firmware_checked(
                        self.device_name, dt_util.utcnow()
                    )
                    for check_version in ota_info.data or ():
                        if check_version.device_id == handle.iot_id:
                            device.apply_version_check(check_version)

        # After the firmware work, not before: this lookup is informational and
        # must not be able to cost the firmware check its turn.
        await self._async_ensure_capabilities(device)

        if device.mower_state.model_id != "":
            self.update_interval = DEVICE_VERSION_INTERVAL

        return device

    async def _async_setup(self) -> None:
        """Set up device version coordinator."""
        await super()._async_setup()
        await self._async_ensure_startup_reads()

    async def _async_device_reported_in(self) -> None:
        """Check for new firmware on first contact if the last check has aged out.

        The poll runs weekly, so a device that was offline when its turn came
        waits out another whole interval before anyone looks again.  There is
        no online/offline edge to hang this on: ``self.device`` is the account
        record, shared by every coordinator of this device, and nothing ever
        marks it offline.  The week-old test is its own rate limiter instead —
        a completed check records a timestamp, so this fires at most once a
        week per device however many pushes arrive.
        """
        if not self._firmware_check_due():
            return
        # A failed check records nothing, so attempts are throttled separately
        # or every later push would retry it.
        self._firmware_check_attempted = dt_util.utcnow()
        LOGGER.debug(
            "%s: firmware check has aged out, refreshing now", self.device_name
        )
        await self.async_request_refresh()

    async def _async_ensure_capabilities(self, device: MowingDevice) -> None:
        """Fetch this model's work-setting schema once, then read it from the store.

        The cloud describes which settings a model exposes and the bounds of
        each, where the integration otherwise hard-codes them.  It describes
        the hardware rather than the moment, so it is fetched once per model
        and persisted, and two mowers of one model share the copy.

        Informational only: every failure here is swallowed, because a mower is
        perfectly usable without it and this must not strand the entities.
        """
        product_key = self.device.product_key
        int_mod = device.mower_state.internal_model
        firmware = device.device_firmwares.device_version
        # intMod identifies the model, and the endpoint rejects it or the
        # version blank, so there is nothing to ask until the device reports.
        if not product_key or not int_mod or not firmware:
            return

        # The schema is firmware-dependent — under a model's minProductVersion
        # the cloud serves none at all — so a copy fetched at an older firmware
        # is re-fetched rather than served for the life of the device.
        stored = self._store.model_capabilities(product_key, int_mod)
        if stored is not None and stored.get("fetched_for_version") == firmware:
            return
        if self._capability_miss.get((product_key, int_mod)) == firmware:
            return
        if not self.cloud_http_usable:
            return
        http = self.manager.mammotion_http
        if http is None:
            return

        try:
            params = await self._cloud_api_call(
                http.get_product_params(
                    product_key, int_mod=int_mod, device_version=firmware
                )
            )
        except Exception:  # noqa: BLE001
            LOGGER.debug(
                "%s: work-setting schema lookup failed",
                self.device_name,
                exc_info=True,
            )
            return

        if params is None or not params.detail_vos:
            # Many models legitimately have none; remember that for this
            # firmware so the poll stops re-asking every interval.
            self._capability_miss[(product_key, int_mod)] = firmware
            LOGGER.debug(
                "%s: no work-setting schema for %s/%s at %s",
                self.device_name,
                product_key,
                int_mod,
                firmware,
            )
            return

        LOGGER.debug(
            "%s: stored %d work-setting parameters for %s/%s",
            self.device_name,
            len(params.detail_vos),
            product_key,
            int_mod,
        )
        self._capability_cache.pop((product_key, int_mod), None)
        await self._store.async_set_model_capabilities(
            product_key,
            int_mod,
            {**params.to_dict(), "fetched_for_version": firmware},
        )

    def capability(self, code: str) -> ProductParam | None:
        """Return the cloud's description of one work setting, when it is known.

        ``code`` is what ``WorkingSettingManage`` switches on in the app: "3"
        is blade height, "12" the bypass strategy, "15" ride-boundary distance.

        The bounds are advisory and must not be used to clamp: the cloud
        reports ``max`` 0.6 for work speed on models that run at 1.0.
        """
        int_mod = getattr(getattr(self.data, "mower_state", None), "internal_model", "")
        product_key = self.device.product_key
        if not int_mod or not product_key:
            return None
        cached = self._capability_cache.get((product_key, int_mod))
        if cached is None:
            stored = self._store.model_capabilities(product_key, int_mod)
            if stored is None:
                return None
            # Parsed once per model: a lookup runs per entity per update.
            cached = ProductParamData.from_dict(stored).by_code()
            self._capability_cache[(product_key, int_mod)] = cached
        return cached.get(code)

    def _firmware_check_due(self) -> bool:
        """Return True when the cloud could tell us something new right now."""
        # The check is an HTTP call against the account; with no usable cloud
        # login there is no source to ask, however old the last answer is.
        if not self.cloud_http_usable:
            return False
        now = dt_util.utcnow()
        attempted = self._firmware_check_attempted
        if attempted is not None and now - attempted < FIRMWARE_CHECK_RETRY_INTERVAL:
            return False
        last = self._store.firmware_checked_at(self.device_name)
        return last is None or now - last >= DEVICE_VERSION_INTERVAL

    async def _async_startup_reads(self) -> None:
        """Fill in whichever firmware and model fields are still unknown."""
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
                with contextlib.suppress(DeviceOfflineException):
                    await self.async_send_and_wait(command, expected_field)

            if not device.mower_state.wifi_mac:
                await self.async_send_command("get_device_network_info")

            handle = self.manager.mower(self.device_name)
            if handle is not None and self.cloud_http_usable:
                http = self.manager.mammotion_http
                if http is not None:
                    ota_info = await self._cloud_api_call(
                        http.get_device_ota_firmware([handle.iot_id])
                    )
                    device = self.manager.get_device_by_name(self.device_name)
                    if (
                        device is not None
                        and ota_info is not None
                        and (check_versions := ota_info.data)
                    ):
                        for check_version in check_versions:
                            if check_version.device_id == handle.iot_id:
                                device.apply_version_check(check_version)

            self.async_set_updated_data(self.data)
        except DeviceOfflineException:
            pass


class MammotionMapUpdateCoordinator(MammotionBaseUpdateCoordinator[MowerInfo]):
    """Class to manage fetching mammotion data."""

    _dynamics_line_cancel: CALLBACK_TYPE | None = None

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
        except ConcurrentRequestError, NoTransportAvailableError:
            pass

        _d = self.manager.get_device_by_name(self.device_name)
        assert _d is not None
        return _d.mower_state

    def _device_supports_dynamics_line(self) -> bool:
        """Return True if this device supports the dynamics-line mow-progress stream."""
        device = self.manager.get_device_by_name(self.device_name)
        firmware = device.device_firmwares.main_controller if device else None
        return DeviceType.value_of_str(self.device_name).is_support_dynamics_line(
            firmware
        )

    def _ble_is_connected(self) -> bool:
        """Return True if BLE transport exists and is currently connected."""
        if handle := self.manager.mower(self.device_name):
            if ble := handle.get_transport(TransportType.BLE):
                return ble.is_connected
        return False

    def _stop_dynamics_line_poll(self) -> None:
        if self._dynamics_line_cancel is not None:
            self._dynamics_line_cancel()
            self._dynamics_line_cancel = None

    async def _on_sys_status_changed_dynamics(self, sys_status: int) -> None:
        """Start the dynamics-line poll when mowing over BLE; stop it otherwise."""
        if (
            sys_status in MOWING_ACTIVE_MODES
            and self._device_supports_dynamics_line()
            and self._ble_is_connected()
        ):
            if self._dynamics_line_cancel is None:
                self._dynamics_line_cancel = async_track_time_interval(
                    self.hass,
                    self._fetch_dynamics_line,
                    DYNAMICS_LINE_INTERVAL,
                )
        else:
            self._stop_dynamics_line_poll()

    async def _fetch_dynamics_line(self, _now: datetime.datetime) -> None:
        """Fetch the dynamics line; self-cancels if BLE has disconnected."""
        if not self._ble_is_connected():
            self._stop_dynamics_line_poll()
            return
        try:
            await self.manager.get_dynamics_line(self.device_name)
            device = self.manager.get_device_by_name(self.device_name)
            if device is not None:
                self.async_set_updated_data(device.mower_state)
        except (
            DeviceOfflineException,
            NoTransportAvailableError,
            GatewayTimeoutException,
        ):
            pass

    async def _async_setup(self) -> None:
        """Set up coordinator with initial call to get map data."""
        await super()._async_setup()
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return

        # Dropped: pymammotion's own dynamics_line_loop (BLE-gated, firmware-gated,
        # is_saga_active-guarded) now owns dynamics-line polling. Leaving this
        # registration active gave a second, unguarded 10s poller that stacked the
        # same common_data_fetch saga. Commented out rather than deleted so the
        # HA-side poller can be restored if the library loop is ever removed.
        # if handle := self.manager.mower(self.device_name):
        #     handle.watch_field(
        #         lambda s: s.raw.report_data.dev.sys_status,
        #         self._on_sys_status_changed_dynamics,
        #     )

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
            error_code = abs(next(iter(self.data.errors.err_code_list)))
        except StopIteration:
            return "No Error"
        info = self.describe_error_code(error_code)
        return info["text"] if info and info["text"] else "Error message not found"

    async def _async_update_data(self) -> MowingDevice:
        """Get data from the device."""
        if data := await super()._async_update_data():
            return data
        device = self.manager.get_device_by_name(self.device_name)
        assert device is not None
        try:
            await self._async_refresh_error_codes()
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

        await self._async_ensure_startup_reads()

    async def _async_startup_reads(self) -> None:
        """Read the two error registers and, if needed, the code table."""
        device = self.manager.get_device_by_name(self.device_name)
        if device is None:
            return
        try:
            await self.async_send_and_wait(
                "read_write_device", "bidire_comm_cmd", rw_id=5, rw=1, context=2
            )
            await self.async_send_and_wait(
                "read_write_device", "bidire_comm_cmd", rw_id=5, rw=1, context=3
            )
            await self._async_refresh_error_codes()

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
        restored_data: Mapping[str, Any] | None = await self._store.async_device_data(
            self.device_name
        )

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

        if self.cloud_http_usable:
            http = self.manager.mammotion_http
            if http is not None:
                try:
                    ota_info = await self._cloud_api_call(
                        http.get_device_ota_firmware([self.device.iot_id])
                    )
                    if ota_info is not None and (check_versions := ota_info.data):
                        for check_version in check_versions:
                            if check_version.device_id == self.device.iot_id:
                                self.data.apply_version_check(check_version)
                except DeviceOfflineException, GatewayTimeoutException:
                    pass

        self._sync_firmware_to_registry()
        return self.data

    def _sync_firmware_to_registry(self) -> None:
        """Push a changed firmware version onto the device-registry entry.

        ``device_info`` is only read when the entity is first registered, so a
        version that arrives later — and for an RTK it always does, since the
        base station reports it over MQTT well after setup — never reaches the
        card.  The mower has ``check_firmware_version`` for this; that one
        reads ``mower_state``, which an RTK has no equivalent of.
        """
        version = self.data.device_version
        if not version:
            return
        device_registry = dr.async_get(self.hass)
        device_entry = device_registry.async_get_device(
            identifiers={(DOMAIN, self.unique_name)}
        )
        if device_entry is None or device_entry.sw_version == version:
            return
        device_registry.async_update_device(device_entry.id, sw_version=version)

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

        if self.cloud_http_usable:
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

    @cached_property
    def device_type(self) -> DeviceType:
        """Return the resolved Spino variant.

        The pool models are sold under names the prefix table covers only in
        part, so the product key identifies the ones the name cannot.
        """
        return DeviceType.value_of_str(self.device_name, self.device.product_key)

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
        # async_restore_data restores a bare device, so re-seed the cloud identity.
        if handle := self.manager.pool_cleaner_device(self.device_name):
            updated = cast(PoolCleanerDevice, handle.snapshot.raw)
            updated.product_key = self.device.product_key
            updated.iot_id = self.device.iot_id
            updated.name = self.device.device_name
            handle.state_machine.apply(updated, handle.availability)
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
            # Fetch pool geometry once at startup.  Responses arrive as unsolicited
            # APP_DOWNLINK_CMD frames and are reassembled by PoolStateReducer.
            for fetch in (self.async_fetch_pool_map, self.async_fetch_pool_line):
                with contextlib.suppress(
                    GatewayTimeoutException,
                    NoTransportAvailableError,
                ):
                    await fetch()
        except DeviceOfflineException:
            self.device.online = False

    async def get_coordinator_data(
        self, device: PoolCleanerDevice
    ) -> PoolCleanerDevice:
        """Return the current pool cleaner state tracked by this coordinator."""
        return self.data

    async def async_restore_data(self) -> None:
        """Restore saved data."""
        restored_data: Mapping[str, Any] | None = await self._store.async_device_data(
            self.device_name
        )

        handle = self.manager.pool_cleaner_device(self.device_name)

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
        except IndexError:
            return "No Error"
        info = self.describe_error_code(error_code)
        return info["text"] if info and info["text"] else "Error message not found"

    async def _async_update_data(self) -> PoolCleanerDevice:
        """Return current pool cleaner state from the device handle.

        Runtime state (sys_status, work_mode, battery, settings, map) is pushed
        into the state machine by ``PoolStateReducer`` as MQTT frames arrive, so
        the only polling work here is the HTTP OTA firmware check, which is not
        pushed over MQTT.
        """
        handle = self.manager.pool_cleaner_device(self.device_name)
        if handle is None:
            return self.data

        if self.cloud_http_usable:
            http = self.manager.mammotion_http
            if http is not None:
                try:
                    ota_info = await self._cloud_api_call(
                        http.get_device_ota_firmware([self.device.iot_id])
                    )
                    if ota_info is not None and (check_versions := ota_info.data):
                        for check_version in check_versions:
                            if check_version.device_id == self.device.iot_id:
                                self.data.apply_version_check(check_version)
                    await self._async_refresh_error_codes()
                except DeviceOfflineException, GatewayTimeoutException:
                    pass

        self.async_save_data(self.data)

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
        await self.async_send_command(
            "set_swimming_work_mode",
            priority=Priority.USER,
            work_mode=work_mode,
        )

    async def async_set_wall_material(self, material: int) -> None:
        """Set the pool wall material."""
        await self.async_send_command(
            "sp_environment_update",
            priority=Priority.USER,
            material=material,
        )

    async def async_set_bottom_type(self, bottom_type: int) -> None:
        """Set the pool bottom shape type."""
        await self.async_send_command(
            "sp_set_bottom_type",
            priority=Priority.USER,
            bottom_type=bottom_type,
        )

    async def async_set_floor_speed(self, speed: float) -> None:
        """Set the pool floor cleaning speed."""
        await self.async_send_command(
            "sp_speed_update",
            priority=Priority.USER,
            speed=speed,
        )

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
        await self.async_send_command(
            "create_spino_plan",
            priority=Priority.USER,
            plan=plan_with_id,
        )

    async def async_edit_spino_task(self, plan: PoolPlan) -> None:
        """Edit an existing Spino schedule (``cmd = EDIT = 4``)."""
        await self.async_send_command(
            "edit_spino_plan",
            priority=Priority.USER,
            plan=plan,
        )

    async def async_rename_spino_task(self, jobid: int, new_name: str) -> None:
        """Rename the Spino schedule identified by ``jobid`` to ``new_name``."""
        plan = self._lookup_spino_plan(jobid)
        await self.async_send_command(
            "rename_spino_plan",
            priority=Priority.USER,
            plan=plan,
            new_name=new_name,
        )

    async def async_set_spino_task_enabled(self, jobid: int, enabled: bool) -> None:
        """Flip the enabled flag on an existing Spino schedule.

        Round-trips the stored plan; the wire inversion (``enable = 0 if
        enabled else 1``) happens in the pymammotion builder.
        """
        plan = self._lookup_spino_plan(jobid)
        await self.async_send_command(
            "enable_spino_plan",
            priority=Priority.USER,
            plan=plan,
            enabled=enabled,
        )

    async def async_delete_spino_task(self, jobid: int) -> None:
        """Delete the Spino schedule identified by ``jobid``."""
        await self.async_send_command(
            "delete_spino_plan",
            priority=Priority.USER,
            jobid=jobid,
        )

    async def async_copy_spino_task(
        self, jobid: int, new_name: str | None = None
    ) -> None:
        """Duplicate the Spino schedule under a new jobid + auto-generated name."""
        plan = self._lookup_spino_plan(jobid)
        existing_names = {p.jobname for p in self.data.plans.values()}
        resolved_name = new_name or make_copy_name(existing_names)
        await self.async_send_command(
            "copy_spino_plan",
            priority=Priority.USER,
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
            "read_write_device",
            priority=Priority.USER,
            rw_id=int(toggle),
            context=int(enabled),
            rw=1,
        )
