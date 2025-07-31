"""Mammotion camera entities."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.camera import (
    Camera,
    CameraEntityDescription,
    StreamType,
    WebRTCSendMessage,
)
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.http.model.camera_stream import (
    StreamSubscriptionResponse,
)
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
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
        stream_fn=lambda coordinator: coordinator.get_stream_subscription(),
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
                if stream_data:
                    _LOGGER.debug("Received stream data: %s", stream_data)
                    entities.extend(
                        MammotionWebRTCCamera(
                            mower.reporting_coordinator, entity_description
                        )
                        for entity_description in CAMERAS
                    )
                else:
                    _LOGGER.error("No Agora data for %s", mower.device.deviceName)
            except Exception as e:
                _LOGGER.error("Error on config camera for: %s", e)

    async_add_entities(entities)
    await async_setup_platform_services(hass, entry)


class MammotionWebRTCCamera(MammotionBaseEntity, Camera):
    """Mammotion WebRTC camera entity."""

    entity_description: MammotionCameraEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionCameraEntityDescription,
    ) -> None:
        """Initialize the WebRTC camera entity."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._stream_data: StreamSubscriptionResponse | None = None
        self._attr_model = coordinator.device.deviceName
        self.access_tokens = [secrets.token_hex(16)]
        self._webrtc_provider = None  # Avoid crash on async_refresh_providers()
        self._legacy_webrtc_provider = None
        self._supports_native_sync_webrtc = False
        self._supports_native_async_webrtc = False

    @property
    def frontend_stream_type(self) -> StreamType | None:
        """Return the type of stream supported by this camera."""
        return StreamType.WEB_RTC

    @property
    def content_type(self) -> str:
        """Return the content type of the camera image."""
        return "image/jpeg"

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        # WebRTC cameras typically don't support still images
        return None

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Handles the WebRTC offer from the browser.

        This function is required by the Home Assistant interface,
        but it will not actually be used because we are using the Agora SDK.
        """
        _LOGGER.warning(
            "A native WebRTC offer from Home Assistant was received, "
            "but it will be ignored because we are using the Agora SDK directly in the frontend."
        )

        # Informs the frontend that it must use the Agora SDK
        send_message(
            '{"type":"error","error":"Use the Agora SDK for this camera","useAgoraSDK":true}',
            session_id,
        )


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
                        f"Invalid speed value for {entity_id}: {speed_value}. Must be between 0 and 1. Using default."
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    f"Invalid speed format for {entity_id}: {call.data['speed']}. Must be a number. Using default."
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
                        f"Invalid speed value for {entity_id}: {speed_value}. Must be between 0 and 1. Using default."
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    f"Invalid speed format for {entity_id}: {call.data['speed']}. Must be a number. Using default."
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
                        f"Invalid speed value for {entity_id}: {speed_value}. Must be between 0 and 1. Using default."
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    f"Invalid speed format for {entity_id}: {call.data['speed']}. Must be a number. Using default."
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
                        f"Invalid speed value for {entity_id}: {speed_value}. Must be between 0 and 1. Using default."
                    )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    f"Invalid speed format for {entity_id}: {call.data['speed']}. Must be a number. Using default."
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
