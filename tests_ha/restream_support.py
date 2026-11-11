"""Builders for the go2rtc re-stream tests: a Yuka's cameras behind a restreamer."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from agora_session_support import CAMERA_DEVICE, make_camera, make_camera_coordinator
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.camera import MammotionWebRTCCamera
from custom_components.mammotion.const import CONF_GO2RTC_URL, DOMAIN
from custom_components.mammotion.restream import Go2RtcRestreamer

GO2RTC_URL = "http://frigate:1984"
GO2RTC_STREAMS = f"{GO2RTC_URL}/api/streams"
HA_INTERNAL_URL = "http://192.168.1.10:8123"
#: go2rtc stream names of a Yuka's three cameras, in CAMERAS slot order.
STREAM_LEFT = "mammotion_yuka_000cld_camera"
STREAM_RIGHT = "mammotion_yuka_000cld_camera_right"
STREAM_REAR = "mammotion_yuka_000cld_camera_rear"
OFFER_SDP = (
    "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\na=candidate:1 1 udp 1 10.0.0.2 5000 typ host\r\n"
)


def make_entry(hass: HomeAssistant, go2rtc_url: str = GO2RTC_URL) -> MockConfigEntry:
    """Add an entry whose options point at a go2rtc server."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_GO2RTC_URL: go2rtc_url})
    entry.add_to_hass(hass)
    return entry


def make_yuka_cameras(
    hass: HomeAssistant, coordinator: MagicMock | None = None
) -> list[MammotionWebRTCCamera]:
    """Build a Yuka's left, right and rear cameras on one coordinator."""
    coordinator = coordinator or make_camera_coordinator()
    assert coordinator.device.device_name == CAMERA_DEVICE
    return [make_camera(hass, coordinator, slot) for slot in range(3)]


def make_restreamer(
    hass: HomeAssistant,
    cameras: list[MammotionWebRTCCamera],
    go2rtc_url: str = GO2RTC_URL,
) -> Go2RtcRestreamer:
    """Build (but do not start) a restreamer for *cameras*."""
    return Go2RtcRestreamer(hass, make_entry(hass, go2rtc_url), go2rtc_url, cameras)


def whep_source(restreamer: Go2RtcRestreamer, name: str) -> str:
    """Return the source go2rtc should hold for feed *name*."""
    token = restreamer.feeds[name].token
    return f"webrtc:{HA_INTERNAL_URL}/api/mammotion/whep/{name}?token={token}"


def idle_stream(source: str) -> dict[str, Any]:
    """Render a go2rtc stream nobody is pulling, as ``/api/streams`` does."""
    return {"producers": [{"url": source}], "consumers": []}


def pulled_stream(source: str) -> dict[str, Any]:
    """Render a go2rtc stream with a running WHEP producer and one consumer."""
    return {
        "producers": [
            {
                "format_name": "webrtc/whep",
                "protocol": "http",
                "url": source.removeprefix("webrtc:"),
            }
        ],
        "consumers": [{"format_name": "rtsp", "remote_addr": "127.0.0.1:50000"}],
    }
