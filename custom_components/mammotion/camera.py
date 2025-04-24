"""Mammotion camera entities."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
import secrets

from homeassistant.components.camera import (
    Camera,
    CameraEntityDescription,
    StreamType,
    WebRTCSendMessage,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.aliyun.model.stream_subscription_response import (
    StreamSubscriptionResponse,
)
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .coordinator import MammotionBaseUpdateCoordinator
from .entity import MammotionBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MammotionCameraEntityDescription(CameraEntityDescription):
    """Describes Mammotion camera entity."""

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
    
    for mower in mowers:
        if not DeviceType.is_luba1(mower.device.deviceName):
            _LOGGER.debug("Configurazione camera per %s", mower.device.deviceName)
            try:
                # Ottieni i dati di streaming
                stream_data = await mower.api.get_stream_subscription(mower.device.deviceName)
                if stream_data:
                    _LOGGER.debug("Dati di streaming ricevuti: %s", stream_data)
                    async_add_entities(
                        MammotionWebRTCCamera(mower.reporting_coordinator, entity_description)
                        for entity_description in CAMERAS
                    )
                else:
                    _LOGGER.error("Nessun dato di streaming disponibile per %s", mower.device.deviceName)
            except Exception as e:
                _LOGGER.error("Errore nella configurazione della camera: %s", e)
                
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
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key
        self._stream_data: StreamSubscriptionResponse | None = None

        self.access_tokens = [secrets.token_hex(16)]
        self._webrtc_provider = None      # Evita crash su async_refresh_providers()
        self._legacy_webrtc_provider = None
        self._supports_native_sync_webrtc = False
        self._supports_native_async_webrtc = False

    @property
    def frontend_stream_type(self) -> StreamType | None:
        """Return the type of stream supported by this camera."""
        return StreamType.WEB_RTC

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        if self._stream_data is None:
            try:
                self._stream_data = self.coordinator.manager.get_stream_subscription(
                    self.coordinator.device_name
                )
            except Exception as e:
                _LOGGER.error("Errore nel recupero dei dati di streaming: %s", e)
                return {}
        
        if not self._stream_data:
            return {}            
            
        # Restituisci tutti i dati necessari per l'SDK Agora
        return {
            "app_id": self._stream_data.data.appid,
            "channel_name": self._stream_data.data.channelName,
            "uid": self._stream_data.data.uid,
            "token": self._stream_data.data.token,
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        # WebRTC cameras typically don't support still images
        return None

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Gestisce l'offerta WebRTC dal browser.
        
        Questa funzione è richiesta dall'interfaccia di Home Assistant,
        ma non verrà effettivamente utilizzata poiché useremo l'SDK Agora.
        """
        _LOGGER.warning(
            "L'offerta WebRTC nativa di Home Assistant è stata ricevuta, "
            "ma verrà ignorata poiché utilizziamo l'SDK Agora direttamente nel frontend."
        )
        
        # Informa il frontend che deve usare l'SDK Agora
        send_message('{"type":"error","error":"Usa l\'SDK Agora per questa telecamera","useAgoraSDK":true}', session_id)


#Global
async def async_setup_platform_services(hass: HomeAssistant, entry: MammotionConfigEntry) -> None:
    """Registra i servizi personalizzati per lo streaming."""

    def _get_mower_by_entity_id(entity_id: str):
        for mower in entry.runtime_data:
                return mower
        return None

    async def handle_refresh_stream(call):
        entity_id = call.data["entity_id"]
        mower = _get_mower_by_entity_id(entity_id)
        if mower:
            stream_data = await mower.get_stream_subscription(mower.device.deviceName)
            mower.reporting_coordinator.set_stream_data(stream_data)
            mower.reporting_coordinator.async_update_listeners()

    async def handle_start_video(call):
        entity_id = call.data["entity_id"]
        mower = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.device_agora_join_channel_with_position(1)

    async def handle_stop_video(call):
        entity_id = call.data["entity_id"]
        mower = _get_mower_by_entity_id(entity_id)
        if mower:
            await mower.api.device_agora_join_channel_with_position(0)

    hass.services.async_register("mammotion", "refresh_stream", handle_refresh_stream)
    hass.services.async_register("mammotion", "start_video", handle_start_video)
    hass.services.async_register("mammotion", "stop_video", handle_stop_video)