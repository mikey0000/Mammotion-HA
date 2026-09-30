"""Mammotion camera entities."""

from __future__ import annotations

import asyncio
import collections
import functools
import logging
import secrets
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homeassistant.components.camera import (
    CameraEntityDescription,
    WebRTCAnswer,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.components.web_rtc import (
    async_register_ice_servers,
)
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyagorartc import (
    AgoraSession,
    APResponse,
    CloseReason,
    IceCandidate,
    PyAgoraRTCError,
    SessionOptions,
)
from pymammotion.http.model.camera_stream import (
    StreamSubscriptionResponse,
)
from pymammotion.http.model.http import Response
from pymammotion.utility.device_type import DeviceType
from webrtc_models import RTCIceCandidateInit, RTCIceServer

from . import MammotionConfigEntry
from .const import DOMAIN
from .coordinator import MammotionBaseUpdateCoordinator
from .entity import MammotionCameraBaseEntity
from .models import MammotionMowerData
from .stream_session import mammotion_credentials, to_ice_candidate, to_rtc_ice_servers

_LOGGER = logging.getLogger(__name__)

PLACEHOLDER = Path(__file__).parent / "placeholder.png"

_CLOSE_MESSAGES = {
    CloseReason.GATEWAY_QUIT: "Another camera on this mower took over the stream",
    CloseReason.DEADLINE: "4G streaming budget exhausted",
}


@dataclass(frozen=True, kw_only=True)
class MammotionCameraEntityDescription(CameraEntityDescription):
    """Describes Mammotion camera entity."""

    key: str
    stream_fn: Callable[
        [MammotionBaseUpdateCoordinator[Any]], Response[StreamSubscriptionResponse]
    ]
    # Agora uid the mower publishes this feed under: cameraStates slot + 1.
    target_uid: int
    # Whether a mower (by device name) has this camera at all.
    exists_fn: Callable[[str], bool] = lambda _device_name: True


# One description per cameraStates slot, in slot order.
CAMERAS: tuple[MammotionCameraEntityDescription, ...] = (
    MammotionCameraEntityDescription(
        key="webrtc_camera",
        stream_fn=lambda coordinator: coordinator.get_stream_data(),
        target_uid=1,
    ),
    MammotionCameraEntityDescription(
        key="webrtc_camera_right",
        stream_fn=lambda coordinator: coordinator.get_stream_data(),
        target_uid=2,
    ),
    MammotionCameraEntityDescription(
        key="webrtc_camera_rear",
        stream_fn=lambda coordinator: coordinator.get_stream_data(),
        target_uid=3,
        exists_fn=lambda device_name: DeviceType.value_of_str(device_name).is_yu_ka(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion camera entities.

    The stream token is minted through the cloud, so a mower without an
    ``iot_id`` (BLE-only, no account) gets no camera at all.
    """
    mowers = [
        mower
        for mower in entry.runtime_data.mowers
        if mower.device.iot_id and not DeviceType.is_luba1(mower.device.device_name)
    ]
    if not mowers:
        return

    entities: list[MammotionWebRTCCamera] = []
    ice_servers = []

    (
        stream_data,
        agora_response,
    ) = await mowers[0].reporting_coordinator.async_check_stream_expiry()

    if agora_response is not None:
        ice_servers = to_rtc_ice_servers(agora_response)

    for mower in mowers:
        _LOGGER.debug("Config camera for %s", mower.device.device_name)
        mower.reporting_coordinator.ice_servers = ice_servers

        entities.extend(
            MammotionWebRTCCamera(mower.reporting_coordinator, description, hass)
            for description in CAMERAS
            if description.exists_fn(mower.device.device_name)
        )
    async_add_entities(entities)
    await async_setup_platform_services(hass, entry)


class MammotionWebRTCCamera(MammotionCameraBaseEntity):
    """Mammotion WebRTC camera entity."""

    entity_description: MammotionCameraEntityDescription
    _attr_capability_attributes = None
    _unregister_ice_servers: Callable[[], None] | None = None

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator[Any],
        entity_description: MammotionCameraEntityDescription,
        hass: HomeAssistant,
    ) -> None:
        """Initialize the WebRTC camera entity."""
        super().__init__(coordinator, entity_description.key)
        self._cache: dict[str, Any] = {}
        self.access_tokens: collections.deque[str] = collections.deque([], 2)
        self.async_update_token()
        self._create_stream_lock: asyncio.Lock | None = None
        self._join_lock = asyncio.Lock()
        self.coordinator = coordinator
        # One per offer; a new offer closes the previous one.
        self._session: AgoraSession | None = None
        # The offer being negotiated and the candidates the browser sent for it;
        # None once its join has started, since Agora has no trickle message.
        self._pending_offer_id: str | None = None
        self._early_candidates: list[IceCandidate] | None = []
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._stream_data: StreamSubscriptionResponse | None = None
        self._sessions: dict[str, WebRTCSendMessage] = {}
        self._teardown_lock = asyncio.Lock()
        self._attr_model = coordinator.device.device_name
        self.access_tokens = [secrets.token_hex(16)]

    async def async_added_to_hass(self) -> None:
        """Let the coordinator drive this entity's stream teardown."""
        await super().async_added_to_hass()
        self.coordinator.register_webrtc_session_control(
            self, self.entity_description.key
        )
        # Core appends the getter to a global list and hands back the only way to
        # take it off again; without releasing it every reload leaves another copy
        # behind and the browser gathers duplicate relay candidates for each.
        self._unregister_ice_servers = async_register_ice_servers(
            self.hass, self.get_ice_servers
        )

    async def async_will_remove_from_hass(self) -> None:
        """Tear the stream down on unload/reload so it cannot outlive the entity."""
        self.coordinator.register_webrtc_session_control(
            None, self.entity_description.key
        )
        if self._unregister_ice_servers is not None:
            self._unregister_ice_servers()
            self._unregister_ice_servers = None
        had_viewers = bool(self._sessions)
        self._sessions.clear()
        if had_viewers:
            await self.async_close_webrtc_session()
        else:
            await self.async_teardown_stream(
                stop_device=not self.coordinator.has_active_camera_sessions
            )
        await super().async_will_remove_from_hass()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a placeholder image for WebRTC cameras that don't support snapshots."""
        return await self.hass.async_add_executor_job(self.placeholder_image)

    @classmethod
    @functools.cache
    def placeholder_image(cls) -> bytes:
        """Return placeholder image to use when no stream is available."""
        return PLACEHOLDER.read_bytes()

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Answer a WebRTC offer by joining this feed's Agora channel.

        This replaces the JavaScript SDK: pyagorartc performs the negotiation.
        """

        if self._join_lock.locked():
            _LOGGER.warning(
                "WebRTC offer already in progress for session %s — ignoring duplicate",
                session_id,
            )
            send_message(WebRTCError("409", "WebRTC negotiation already in progress"))
            return

        async with self._join_lock:
            self._pending_offer_id = session_id
            self._early_candidates = []
            # Tracked before the token request starts the mower's stream, so a
            # close mid-negotiation or a failed offer still stops it.
            self._sessions[session_id] = send_message
            await self.coordinator.async_register_camera_session(
                self.entity_description.key
            )
            answered = False
            try:
                answered = await self._async_answer_offer(
                    offer_sdp, session_id, send_message
                )
            finally:
                self._pending_offer_id = None
                if not answered:
                    self.close_webrtc_session(session_id)
                elif not self._sessions:
                    # The viewer closed while negotiation was still running.
                    await self.async_close_webrtc_session()

    async def _async_answer_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> bool:
        """Negotiate with Agora and send the answer; return whether one was sent."""
        (
            stream_data,
            agora_response,
        ) = await self.coordinator.async_check_stream_expiry(
            # A fresh token per join, as the app does.  It does not stop Agora
            # quitting an established session on the same viewer uid (2003);
            # only joins racing within about a second coexist.
            force=True
        )
        await self.coordinator.async_send_command("send_todev_ble_sync", sync_type=3)
        _LOGGER.info("Handling WebRTC offer for session %s", session_id)

        if not stream_data:
            _LOGGER.error("No stream data available for WebRTC offer")
            send_message(
                WebRTCError("500", "No stream data available for WebRTC offer")
            )
            return False

        if (
            self.entity_description.target_uid != 1
            and not self.coordinator.all_cameras_streaming
        ):
            send_message(WebRTCError("503", "Vision stream unavailable"))
            return False

        if agora_response is None:
            _LOGGER.error("No Agora edge available for WebRTC offer")
            send_message(WebRTCError("500", "No Agora edge available for WebRTC offer"))
            return False

        await self._async_close_session()
        try:
            # Built in here: a malformed token (salt, uid) must reach the viewer as a 500.
            session = self._new_session(stream_data, agora_response)
            candidates, self._early_candidates = self._early_candidates, None
            for candidate in candidates or ():
                session.add_ice_candidate(candidate)
            self._session = session
            answer_sdp = await session.join(offer_sdp, session_id)
        except (PyAgoraRTCError, ValueError, TypeError) as ex:
            _LOGGER.warning(
                "WebRTC negotiation failed for session %s: %s: %s",
                session_id,
                type(ex).__name__,
                ex,
            )
            send_message(WebRTCError("500", f"WebRTC negotiation failed: {ex}"))
            return False

        send_message(WebRTCAnswer(answer_sdp))
        _LOGGER.info("WebRTC negotiation completed successfully")
        return True

    def _new_session(
        self, data: StreamSubscriptionResponse, agora_response: APResponse
    ) -> AgoraSession:
        """Build the session for one offer on this feed's publisher uid."""
        if data.openEncrypt:
            _LOGGER.warning(
                "Stream token for %s has openEncrypt=%s; pyagorartc cannot decrypt "
                "the channel, so expect no picture",
                self.coordinator.device.device_name,
                data.openEncrypt,
            )
        deadline = None
        # WiFi streams stay unbounded: the cloud's budget only meters cellular data.
        if self.coordinator.is_on_4g and data.availableTime and data.availableTime > 0:
            deadline = time.monotonic() + data.availableTime
        return AgoraSession(
            mammotion_credentials(data),
            agora_response,
            options=SessionOptions(
                client_codec="vp8", target_uid=self.entity_description.target_uid
            ),
            on_peer_left=self._on_peer_left,
            on_closed=self._on_closed,
            keepalive=self._fpv_keepalive,
            deadline=deadline,
            clock=time.monotonic,
            spawn=self._spawn_session_task,
        )

    def _spawn_session_task(
        self, coro: Coroutine[object, object, None]
    ) -> asyncio.Task[None]:
        """Run a session task as a Home Assistant background task."""
        return self.hass.async_create_background_task(
            coro, f"{DOMAIN} agora {self.entity_id}"
        )

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Collect WebRTC candidates for inclusion in the join message."""
        if session_id != self._pending_offer_id:
            # Agora has no trickle message: only candidates known before the join count.
            _LOGGER.debug(
                "Dropping ICE candidate for session %s: not negotiating", session_id
            )
            return
        if self._early_candidates is None:
            _LOGGER.debug(
                "Dropping ICE candidate for session %s: its join has started",
                session_id,
            )
            return
        self._early_candidates.append(to_ice_candidate(candidate))

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Close a WebRTC session.

        Home Assistant calls this synchronously when the frontend drops its
        subscription, so the actual teardown is scheduled.  The name matters:
        core only ever invokes ``close_webrtc_session`` on a native WebRTC
        camera (``camera/webrtc.py`` registers it as the subscription's
        teardown), and the base implementation no-ops because native cameras
        have no ``_webrtc_provider``.
        """
        if session_id not in self._sessions:
            return
        del self._sessions[session_id]
        if not self._sessions:
            self.hass.async_create_task(self.async_close_webrtc_session())

    async def async_close_webrtc_session(self) -> None:
        """Leave this feed's channel; stop the mower only when its last feed closes."""
        await self._async_close_session()
        await self.coordinator.async_release_camera_session(self.entity_description.key)

    async def _async_close_session(self) -> None:
        """Close this feed's Agora session; safe from inside its own callbacks."""
        if (session := self._session) is not None:
            self._session = None
            await session.close()

    async def _on_closed(self, reason: CloseReason) -> None:
        """Tell this feed's viewers the session ended on its own, then release it."""
        # A host close and a failed join are already on their own teardown path.
        if reason in (CloseReason.CLOSED_BY_HOST, CloseReason.JOIN_FAILED):
            return
        # A replaced session ending mid-offer must not end the viewer now negotiating.
        if self._pending_offer_id is not None:
            return
        viewers = list(self._sessions.values())
        if not viewers:
            return
        self._sessions.clear()
        message = _CLOSE_MESSAGES.get(reason, "Stream lost")
        for send_message in viewers:
            send_message(WebRTCError("503", message))
        await self.async_close_webrtc_session()

    async def async_teardown_stream(self, *, stop_device: bool = True) -> None:
        """Leave the Agora channel, then stop the mower publishing.

        Mirrors the app's ``onDestroy``: ``leaveChannel()`` followed by an
        unconditional ``vi_switch=0``.  That command is not gated on firmware
        version in the app — only the *start* verb (``vi_switch=1``) is, which
        is why ``get_stream_subscription`` withholds it on new firmware while
        the stop half always runs.
        """
        async with self._teardown_lock:
            # A later frontend close for these viewers must not release again.
            self._sessions.clear()
            await self._async_close_session()
            if not stop_device:
                return
            try:
                await self.coordinator.manager.stop_stream(
                    self.coordinator.device.device_name
                )
            except Exception as ex:  # noqa: BLE001 — teardown is best-effort
                _LOGGER.debug("Stop-stream command failed on close: %s", ex)

    async def _fpv_keepalive(self) -> bool:
        """Re-arm the mower's video encoder on 4G; return False on WiFi.

        Invoked by the Agora session every few seconds while it is joined.
        Over cellular the encoder stops publishing unless poked with
        ``refresh_fpv``; on WiFi the stream is continuous, so return False to
        stop the keep-alive loop without sending anything.
        """
        if not self.coordinator.is_on_4g:
            return False
        await self.coordinator.async_send_command("refresh_fpv")
        return True

    async def _on_peer_left(self, uid: int) -> None:
        """Recover this feed after its publisher left the channel and stayed gone."""
        _LOGGER.debug("Agora publisher uid %s left; recovering the stream", uid)
        await self._recover_stream()

    async def _recover_stream(self) -> None:
        """Re-establish the stream after the mower drops out of the Agora channel.

        Nudge the device with a BLE sync, then refresh the stream subscription so
        it rejoins the channel.
        """
        await self.coordinator.async_send_command("send_todev_ble_sync", sync_type=3)
        await self.coordinator.manager.get_stream_subscription(
            self.coordinator.device.device_name,
            self.coordinator.device.iot_id,
            all_cameras=self.coordinator.streams_all_cameras,
        )

    def get_ice_servers(self) -> list[RTCIceServer]:
        """Return the ICE servers from Agora API.

        Read through rather than snapshotted at construction: the coordinator
        rebinds this list whenever it refreshes the stream token, and core
        calls this again for every new WebRTC session.  A session already
        running keeps the servers it negotiated with.
        """
        return self.coordinator.ice_servers


# Global
async def async_setup_platform_services(  # noqa: C901
    hass: HomeAssistant, entry: MammotionConfigEntry
) -> None:
    """Register custom services for streaming."""

    def _get_mower_by_entity_id(entity_id: str) -> MammotionMowerData | None:
        state = hass.states.get(entity_id)
        name = state.attributes.get("model_name")
        return next(
            (
                mower
                for mower in entry.runtime_data.mowers
                if mower.device.device_name == name
            ),
            None,
        )

    async def handle_refresh_stream(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            stream_data = await mower.api.get_stream_subscription(
                mower.device.device_name,
                mower.device.iot_id,
                all_cameras=mower.reporting_coordinator.streams_all_cameras,
            )
            _LOGGER.debug(
                "Refresh stream data: code %s",
                stream_data.code if stream_data else None,
            )

            mower.reporting_coordinator.set_stream_data(stream_data)
            mower.reporting_coordinator.async_update_listeners()

    async def handle_start_video(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.join_webrtc_channel()

    async def handle_stop_video(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.leave_webrtc_channel()

    async def handle_get_tokens(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data["entity_id"]
        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower is not None:
            stream_data = mower.reporting_coordinator.get_stream_data()

            if not stream_data or stream_data.data is None:
                return {}
            # Return all the data needed for the Agora SDK
            return stream_data.data.to_dict()
        return {}

    async def handle_move_forward(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        raw_speed = call.data["speed"]
        use_wifi = call.data.get("use_wifi")
        if raw_speed is not None:
            try:
                speed_value = float(raw_speed)
                if 0.1 <= speed_value <= 1:
                    speed = speed_value
                else:
                    _LOGGER.warning(
                        "Invalid speed value for %s: %s. Must be between 0 and 1. Using default.",
                        entity_id,
                        speed_value,
                    )
            except ValueError, TypeError:
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    raw_speed,
                )

        mower = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_forward(
                speed=speed, use_wifi=use_wifi
            )

    async def handle_move_left(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        raw_speed = call.data["speed"]
        use_wifi = call.data.get("use_wifi")
        if raw_speed is not None:
            try:
                speed_value = float(raw_speed)
                if 0.1 <= speed_value <= 1:
                    speed = speed_value
                else:
                    _LOGGER.warning(
                        "Invalid speed value for %s: %s. Must be between 0 and 1. Using default.",
                        entity_id,
                        speed_value,
                    )
            except ValueError, TypeError:
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    raw_speed,
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_left(
                speed=speed, use_wifi=use_wifi
            )

    async def handle_move_right(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        raw_speed = call.data["speed"]
        use_wifi = call.data.get("use_wifi")
        if raw_speed is not None:
            try:
                speed_value = float(raw_speed)
                if 0.1 <= speed_value <= 1:
                    speed = speed_value
                else:
                    _LOGGER.warning(
                        "Invalid speed value for %s: %s. Must be between 0 and 1. Using default.",
                        entity_id,
                        speed_value,
                    )
            except ValueError, TypeError:
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    raw_speed,
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_right(
                speed=speed, use_wifi=use_wifi
            )

    async def handle_move_backward(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        raw_speed = call.data["speed"]
        use_wifi = call.data.get("use_wifi")
        if raw_speed is not None:
            try:
                speed_value = float(raw_speed)
                if 0.1 <= speed_value <= 1:
                    speed = speed_value
                else:
                    _LOGGER.warning(
                        "Invalid speed value for %s: %s. Must be between 0 and 1. Using default.",
                        entity_id,
                        speed_value,
                    )
            except ValueError, TypeError:
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    raw_speed,
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_back(
                speed=speed, use_wifi=use_wifi
            )

    hass.services.async_register("mammotion", "refresh_stream", handle_refresh_stream)
    hass.services.async_register("mammotion", "start_video", handle_start_video)
    hass.services.async_register("mammotion", "stop_video", handle_stop_video)
    hass.services.async_register(
        "mammotion",
        "get_tokens",
        handle_get_tokens,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("mammotion", "move_forward", handle_move_forward)
    hass.services.async_register("mammotion", "move_left", handle_move_left)
    hass.services.async_register("mammotion", "move_right", handle_move_right)
    hass.services.async_register("mammotion", "move_backward", handle_move_backward)
