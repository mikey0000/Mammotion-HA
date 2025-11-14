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
    CameraEntityDescription,
    WebRTCAnswer,
    WebRTCError,
    WebRTCSendMessage,
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
from pymammotion.http.model.camera_stream import (
    StreamSubscriptionResponse,
)
from pymammotion.utility.device_type import DeviceType
from webrtc_models import RTCIceCandidateInit, RTCIceServer

from . import MammotionConfigEntry
from .agora_api import SERVICE_IDS, AgoraAPIClient
from .agora_websocket import AgoraWebSocketHandler
from .coordinator import MammotionBaseUpdateCoordinator
from .entity import MammotionCameraBaseEntity
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
    mowers = entry.runtime_data.mowers
    entities = []
    for mower in mowers:
        if not DeviceType.is_luba1(mower.device.device_name):
            _LOGGER.debug("Config camera for %s", mower.device.device_name)
            try:
                # Try to get stream data
                stream_data = await mower.api.get_stream_subscription(
                    mower.device.device_name, mower.device.iot_id
                )
                mower.reporting_coordinator._stream_data = stream_data

                if stream_data is not None:
                    _LOGGER.debug("Received stream data: %s", stream_data)

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

                            # Get ICE servers and convert to RTCIceServer format
                            ice_servers_agora = agora_response.get_ice_servers()
                            ice_servers = []
                            _LOGGER.info(
                                "Ice Servers from Agora API:%s", ice_servers_agora
                            )
                            for ice_server in ice_servers_agora:
                                ice_servers.append(
                                    RTCIceServer(
                                        urls=ice_server.urls,
                                        username=ice_server.username,
                                        credential=ice_server.credential,
                                    )
                                )

                            # Store ICE servers in coordinator
                            mower.reporting_coordinator._ice_servers = ice_servers
                            mower.reporting_coordinator._agora_response = agora_response
                            _LOGGER.info(
                                "Retrieved %d ICE servers from Agora API",
                                len(ice_servers),
                            )
                    except Exception as e:
                        _LOGGER.error("Failed to get ICE servers from Agora API: %s", e)
                        mower.reporting_coordinator._ice_servers = []

                    entities.extend(
                        MammotionWebRTCCamera(
                            mower.reporting_coordinator, entity_description, hass
                        )
                        for entity_description in CAMERAS
                    )
                else:
                    _LOGGER.error("No Agora data for %s", mower.device.device_name)
            except Exception as e:
                _LOGGER.error("Error on async setup entry camera for: %s", e)

    async_add_entities(entities)
    await async_setup_platform_services(hass, entry)


class MammotionWebRTCCamera(MammotionCameraBaseEntity):
    """Mammotion WebRTC camera entity."""

    entity_description: MammotionCameraEntityDescription
    _attr_capability_attributes = None

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
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._stream_data: StreamSubscriptionResponse | None = None
        self._attr_model = coordinator.device.device_name
        self.access_tokens = [secrets.token_hex(16)]
        # Get ICE servers from coordinator (populated in async_setup_entry)
        self.ice_servers = getattr(coordinator, "_ice_servers", [])
        self._agora_response = getattr(coordinator, "_agora_response", None)
        async_register_ice_servers(hass, self.get_ice_servers)
        self._add_candidates = True

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
        self._add_candidates = True
        self._agora_handler.candidates = []
        _LOGGER.info("Handling WebRTC offer for session %s", session_id)
        _LOGGER.info("Raw OFFER SDP %s", offer_sdp)

        # if 'candidate' not in offer_sdp:
        #     return
        await asyncio.sleep(5)  # Small delay to ensure readiness
        self._add_candidates = False
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
                # Send set_client_role after successful join
            else:
                send_message(WebRTCError("500", "WebRTC negotiation failed"))

        except (websockets.exceptions.WebSocketException, json.JSONDecodeError) as ex:
            _LOGGER.error("Error handling WebRTC offer: %s", ex)
            send_message(WebRTCError("500", f"Error handling WebRTC offer: {ex}"))

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Ignore WebRTC candidates."""
        # _LOGGER.info("Received WebRTC candidate for session %s", session_id)
        _LOGGER.info("Received WebRTC candidate %s", candidate)
        if self._add_candidates:
            self._agora_handler.add_ice_candidate(candidate)

    @callback
    async def async_close_webrtc_session(self, session_id: str) -> None:
        """Close WebRTC session."""
        await self._agora_handler.disconnect()

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
                agora_data, offer_sdp, session_id, agora_response=self._agora_response
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

    def get_ice_servers(self) -> list[RTCIceServer]:
        """Return the ICE servers from Agora API."""
        return self.ice_servers


# Global
async def async_setup_platform_services(
    hass: HomeAssistant, entry: MammotionConfigEntry
) -> None:
    """Register custom services for streaming."""

    def _get_mower_by_entity_id(entity_id: str):
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

    async def handle_refresh_stream(call) -> None:
        entity_id = call.data["entity_id"]
        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            stream_data = await mower.api.get_stream_subscription(
                mower.device.device_name, mower.device.iot_id
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
        raw_speed = call.data["speed"]
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
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    raw_speed,
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_forward(speed=speed)

    async def handle_move_left(call) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        raw_speed = call.data["speed"]
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
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    raw_speed,
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_left(speed=speed)

    async def handle_move_right(call) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        raw_speed = call.data["speed"]
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
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    raw_speed,
                )

        mower: MammotionMowerData = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.reporting_coordinator.async_move_right(speed=speed)

    async def handle_move_backward(call) -> None:
        entity_id = call.data["entity_id"]

        # Check if speed parameter exists and validate it
        speed = 0.4  # Default speed
        raw_speed = call.data["speed"]
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
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid speed format for %s: %s. Must be a number. Using default.",
                    entity_id,
                    raw_speed,
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
