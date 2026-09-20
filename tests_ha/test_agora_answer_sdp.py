"""The DTLS setup role the answer SDP gives Agora.

The role has to follow whatever Agora reports in its join response: answering
the wrong one leaves both ends waiting for the other to send the ClientHello.

The answer is read back with the same ``sdp_transform`` parser the handler
uses on the browser's offer, so these assert on the negotiated attribute of
every media section rather than on a substring of the blob.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from sdp_transform import parse as sdp_parse

from custom_components.mammotion.agora_websocket import (
    AgoraWebSocketHandler,
    SdpInfo,
)


def _ortc(dtls_role: str) -> dict[str, Any]:
    """Build a join response trimmed to what the answer is built from."""
    return {
        "cname": "o/i14u9pJrxRKAsu",
        "iceParameters": {
            "candidates": [
                {
                    "foundation": "udpcandidate",
                    "ip": "45.196.22.13",
                    "port": 4707,
                    "priority": 2103266323,
                    "protocol": "udp",
                    "type": "host",
                }
            ],
            "iceUfrag": "KdDV",
            "icePwd": "QWsV2C8fBurFPvrQedNCksay",
        },
        "dtlsParameters": {
            "role": dtls_role,
            "fingerprints": [{"algorithm": "sha-256", "fingerprint": "BD:3E:08"}],
        },
        "rtpCapabilities": {
            "sendrecv": {
                "audioCodecs": [
                    {
                        "payloadType": 111,
                        "rtpMap": {"encodingName": "opus", "clockRate": 48000},
                    }
                ],
                "videoCodecs": [
                    {
                        "payloadType": 102,
                        "rtpMap": {"encodingName": "H264", "clockRate": 90000},
                    }
                ],
                "audioExtensions": [],
                "videoExtensions": [],
            }
        },
    }


@pytest.fixture
def handler(hass: HomeAssistant) -> AgoraWebSocketHandler:
    """Build a handler with no session state; only the answer builder is exercised."""
    return AgoraWebSocketHandler(hass=hass)


def _answer_media(handler: AgoraWebSocketHandler, dtls_role: str) -> list[dict]:
    """Answer the offer HA's frontend makes, parsed back into media sections."""
    sdp_info = SdpInfo(
        parsed_sdp={
            "groups": [{"type": "BUNDLE", "mids": "0 1"}],
            "extmapAllowMixed": True,
            "media": [
                {"type": "audio", "mid": "0", "direction": "recvonly"},
                {"type": "video", "mid": "1", "direction": "recvonly"},
            ],
        },
        fingerprint="",
        ice_ufrag="",
        ice_pwd="",
        audio_codecs=[],
        video_codecs=[],
        audio_extensions=[],
        video_extensions=[],
        audio_direction="recvonly",
        video_direction="recvonly",
        ice_candidates=[],
        extmap_allow_mixed=True,
        setup_role="actpass",
    )
    sdp = handler._generate_answer_sdp(_ortc(dtls_role), sdp_info)
    assert sdp is not None
    media = sdp_parse(sdp)["media"]
    assert [section["type"] for section in media] == ["audio", "video"]
    return media


def test_answer_is_passive_when_agora_takes_the_dtls_server_role(
    handler: AgoraWebSocketHandler,
) -> None:
    """Agora as DTLS server means the browser opens the handshake."""
    media = _answer_media(handler, "server")

    assert [section["setup"] for section in media] == ["passive", "passive"]


def test_answer_is_active_when_agora_keeps_the_client_role(
    handler: AgoraWebSocketHandler,
) -> None:
    """Agora reports 'client' in practice, so the answer stays active."""
    media = _answer_media(handler, "client")

    assert [section["setup"] for section in media] == ["active", "active"]
