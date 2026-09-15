"""Tests for the DTLS setup role the answer SDP gives Agora.

The role has to follow whatever Agora reports in its join response: answering
the wrong one leaves both ends waiting for the other to send the ClientHello.
"""

from __future__ import annotations

import types
from typing import Any

import pytest
from test_fpv_keepalive import _load_agora_websocket


@pytest.fixture(scope="module")
def agora_module() -> types.ModuleType:
    """Load the real handler module once per test module."""
    return _load_agora_websocket()


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


def _answer(agora_module: types.ModuleType, dtls_role: str) -> str:
    """Generate an answer to the offer HA's frontend makes."""
    parsed = {
        "groups": [{"type": "BUNDLE", "mids": "0 1"}],
        "extmapAllowMixed": True,
        "media": [
            {"type": "audio", "mid": "0", "direction": "recvonly"},
            {"type": "video", "mid": "1", "direction": "recvonly"},
        ],
    }
    sdp_info = agora_module.SdpInfo(
        parsed_sdp=parsed,
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
    handler = agora_module.AgoraWebSocketHandler.__new__(
        agora_module.AgoraWebSocketHandler
    )
    handler._video_streams = {}
    sdp = agora_module.AgoraWebSocketHandler._generate_answer_sdp(
        handler, _ortc(dtls_role), sdp_info
    )
    assert sdp is not None
    return sdp


def test_answer_is_passive_when_agora_takes_the_dtls_server_role(
    agora_module: types.ModuleType,
) -> None:
    """Agora as DTLS server means the browser opens the handshake."""
    assert "a=setup:passive" in _answer(agora_module, "server")


def test_answer_is_active_when_agora_keeps_the_client_role(
    agora_module: types.ModuleType,
) -> None:
    """Agora reports 'client' in practice, so the answer stays active."""
    sdp = _answer(agora_module, "client")

    assert "a=setup:active" in sdp
    assert "a=setup:passive" not in sdp
