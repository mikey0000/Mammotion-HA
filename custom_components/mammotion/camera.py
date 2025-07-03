"""Mammotion camera entities."""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import websockets
from homeassistant.components.camera import (
    Camera,
    CameraEntityDescription,
    CameraEntityFeature,
    CameraWebRTCProvider,
    StreamType,
    WebRTCAnswer,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.http.model.camera_stream import (
    StreamSubscriptionResponse,
)
from pymammotion.utility.device_type import DeviceType
from webrtc_models import RTCIceCandidateInit

from . import MammotionConfigEntry
from .agora_websocket import AgoraWebSocketHandler
from .coordinator import MammotionBaseUpdateCoordinator
from .entity import MammotionBaseEntity
from .models import MammotionMowerData

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MammotionCameraEntityDescription(CameraEntityDescription):
    """Describes Mammotion camera entity."""

    key: str
    stream_fn: Callable[[MammotionBaseUpdateCoordinator], StreamSubscriptionResponse]


CAMERAS: tuple[MammotionCameraEntityDescription, ...] = (
    MammotionCameraEntityDescription(
        key="webrtc_camera",
        stream_fn=lambda coordinator: coordinator.get_stream_data(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion camera entities."""
    mowers = entry.runtime_data
    entities = []
    for mower in mowers:
        if not DeviceType.is_luba1(mower.device.deviceName):
            _LOGGER.debug("Config camera for %s", mower.device.deviceName)
            try:
                # Try to get stream data
                stream_data = await mower.api.get_stream_subscription(
                    mower.device.deviceName, mower.device.iotId
                )
                mower.reporting_coordinator._stream_data = stream_data

                if stream_data:
                    _LOGGER.debug("Received stream data: %s", stream_data)
                    entities.extend(
                        MammotionWebRTCCamera(
                            mower.reporting_coordinator, entity_description, hass
                        )
                        for entity_description in CAMERAS
                    )
                else:
                    _LOGGER.error("No Agora data for %s", mower.device.deviceName)
            except (OSError, ValueError) as e:
                _LOGGER.error("Error on config camera for: %s", e)

    async_add_entities(entities)
    await async_setup_platform_services(hass, entry)


class MammotionWebRTCCamera(MammotionBaseEntity, Camera):
    """Mammotion WebRTC camera entity."""

    entity_description: MammotionCameraEntityDescription
    _attr_has_entity_name = True
    _attr_name = None
    _attr_is_streaming = True
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_capability_attributes = None
    _supports_native_async_webrtc = True

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionCameraEntityDescription,
        hass: HomeAssistant,
    ) -> None:
        """Initialize the WebRTC camera entity."""
        super().__init__(coordinator, entity_description.key)
        self._cache: dict[str, Any] = {}
        self.access_tokens: collections.deque = collections.deque([], 2)
        self.async_update_token()
        self._create_stream_lock: asyncio.Lock | None = None
        self._agora_handler = AgoraWebSocketHandler(hass)
        self._webrtc_provider: CameraWebRTCProvider | None = None
        self._supports_native_async_webrtc = True
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._stream_data: StreamSubscriptionResponse | None = None
        self._attr_model = coordinator.device.deviceName
        self.access_tokens = [secrets.token_hex(16)]

    @property
    def frontend_stream_type(self) -> StreamType | None:
        """Return the type of stream supported by this camera."""
        return StreamType.WEB_RTC

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a placeholder image for WebRTC cameras that don't support snapshots."""
        return None

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Handle WebRTC offer by initiating WebSocket connection to Agora.

        This replaces the JavaScript SDK functionality and performs the WebRTC
        negotiation directly in Python.
        """
        _LOGGER.info("Handling WebRTC offer for session %s", session_id)

        try:
            # Get stream data (appid, channelName, token, uid)
            stream_data = self.coordinator.get_stream_data()
            if not stream_data or stream_data.data is None:
                _LOGGER.error("No stream data available for WebRTC offer")
                send_message(
                    WebRTCError(
                        "500",
                        "No stream data available for WebRTC offer",
                    )
                )
                return

            agora_data = stream_data.data

            # Start WebSocket connection and WebRTC negotiation
            answer_sdp = await self._perform_webrtc_negotiation(
                offer_sdp, agora_data, session_id
            )

            if answer_sdp:
                # Send the answer back to the browser

                send_message(WebRTCAnswer(answer_sdp))
                _LOGGER.info("WebRTC negotiation completed successfully")
            else:
                send_message(WebRTCError("500", "WebRTC negotiation failed"))

        except (websockets.exceptions.WebSocketException, json.JSONDecodeError) as ex:
            _LOGGER.error("Error handling WebRTC offer: %s", ex)
            send_message(WebRTCError("500", f"Error handling WebRTC offer: {ex}"))

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Ignore WebRTC candidates."""
        return

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Close WebRTC session."""
        return None

    async def _perform_webrtc_negotiation(
        self, offer_sdp: str, agora_data: StreamSubscriptionResponse, session_id: str
    ) -> str | None:
        """Perform WebRTC negotiation through Agora WebSocket.

        Args:
            self: The camera instance
            offer_sdp: The WebRTC offer SDP from the browser
            agora_data: Dict containing appid, channelName, token, uid
            session_id: Session ID for this WebRTC connection

        Returns:
            Answer SDP if successful, None otherwise

        """
        _LOGGER.debug("Starting WebRTC negotiation with Agora data: %s", agora_data)
        # _LOGGER.debug("Starting WebRTC negotiation with offer_sdp data: %s", offer_sdp)

        # Use the new AgoraWebSocketHandler for negotiation
        try:
            answer_sdp = await self._agora_handler.connect_and_join(
                agora_data, offer_sdp, session_id
            )

            if answer_sdp:
                _LOGGER.info("Successfully negotiated WebRTC through Agora")
                return answer_sdp

            _LOGGER.error(
                "Failed to get answer SDP from Agora negotiation, using handler fallback"
            )
            # Use the handler's fallback SDP generation as last resort
            return self._agora_handler._generate_fallback_sdp()

        except (OSError, ValueError, TypeError) as ex:
            _LOGGER.error("WebRTC negotiation failed: %s", ex)
            _LOGGER.warning("Using fallback SDP due to exception")
            return self._agora_handler._generate_fallback_sdp()


# Global
async def async_setup_platform_services(
    hass: HomeAssistant, entry: MammotionConfigEntry
) -> None:
    """Register custom services for streaming."""

    def _get_mower_by_entity_id(entity_id: str):
        state = hass.states.get(entity_id)
        name = state.attributes.get("model_name")
        return next(
            (mower for mower in entry.runtime_data if mower.device.deviceName == name),
            None,
        )

    async def handle_refresh_stream(call) -> None:
        entity_id = call.data["entity_id"]
        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            stream_data = await mower.api.get_stream_subscription(
                mower.device.deviceName, mower.device.iotId
            )
            _LOGGER.debug("Refresh stream data : %s", stream_data)

            mower.reporting_coordinator.set_stream_data(stream_data)
            mower.reporting_coordinator.async_update_listeners()

    async def handle_start_video(call) -> None:
        entity_id = call.data["entity_id"]
        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.join_webrtc_channel()

    async def handle_stop_video(call) -> None:
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

    async def handle_move_forward(call) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        if "speed" in call.data:
            try:
                speed_value = float(call.data["speed"])
                if 0.1 <= speed_value <= 1:
                    speed = speed_value
                else:
                    _LOGGER.warning(
                        "Invalid speed value for %s: %s. Must be between 0 and 1. Using default.",
                        entity_id,
                        speed_value,
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    call.data["speed"],
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_forward(speed=speed)

    async def handle_move_left(call) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        if "speed" in call.data:
            try:
                speed_value = float(call.data["speed"])
                if 0.1 <= speed_value <= 1:
                    speed = speed_value
                else:
                    _LOGGER.warning(
                        "Invalid speed value for %s: %s. Must be between 0 and 1. Using default.",
                        entity_id,
                        speed_value,
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    call.data["speed"],
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_left(speed=speed)

    async def handle_move_right(call) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        if "speed" in call.data:
            try:
                speed_value = float(call.data["speed"])
                if 0.1 <= speed_value <= 1:
                    speed = speed_value
                else:
                    _LOGGER.warning(
                        "Invalid speed value for %s: %s. Must be between 0 and 1. Using default.",
                        entity_id,
                        speed_value,
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    call.data["speed"],
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_right(speed=speed)

    async def handle_move_backward(call) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        if "speed" in call.data:
            try:
                speed_value = float(call.data["speed"])
                if 0.1 <= speed_value <= 1:
                    speed = speed_value
                else:
                    _LOGGER.warning(
                        "Invalid speed value for %s: %s. Must be between 0 and 1. Using default.",
                        entity_id,
                        speed_value,
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    call.data["speed"],
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_back(speed=speed)

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
