"""Mammotion stream tokens and HA WebRTC types translated to pyagorartc's."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agora_session_support import make_ap_response, make_stream_data
from pyagorartc import APRejectedError, ChannelEncryption, IceCandidate
from pymammotion.http.model.camera_stream import StreamSubscriptionResponse
from pymammotion.http.model.http import Response
from webrtc_models import RTCIceCandidateInit, RTCIceServer

from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator
from custom_components.mammotion.stream_session import (
    mammotion_credentials,
    to_ice_candidate,
    to_rtc_ice_servers,
)


def test_a_token_maps_to_channel_credentials() -> None:
    """Budget and camera slots are session concerns, so they are not carried over."""
    creds = mammotion_credentials(make_stream_data(uid="12345678"))

    assert (
        creds.app_id,
        creds.channel_name,
        creds.token,
        creds.uid,
        creds.license,
        creds.encryption,
    ) == ("app-id", "channel-1", "channel-token", 12345678, "license-1", None)
    # The cloud's Android enum name may not be what the web AP accepts.
    assert creds.area_code != "AREA_CODE_EU"


def test_encryption_is_mapped_only_when_the_token_turns_it_on() -> None:
    """The salt arrives base64 and the library wants the raw bytes."""
    enabled = mammotion_credentials(
        make_stream_data(openEncrypt=1, key="secret", salt="c2FsdA==")
    )
    disabled = mammotion_credentials(
        make_stream_data(openEncrypt=0, key="secret", salt="c2FsdA==")
    )

    assert enabled.encryption == ChannelEncryption(
        mode="aes-256-gcm2", secret="secret", salt=b"salt"
    )
    assert disabled.encryption is None


def test_ice_servers_are_the_first_turn_edge_over_three_transports() -> None:
    """Three entries, as the Web SDK hands the browser."""
    servers = to_rtc_ice_servers(make_ap_response())

    assert all(isinstance(server, RTCIceServer) for server in servers)
    assert [server.urls for server in servers] == [
        ["turn:198.51.100.20:3478?transport=udp"],
        ["turn:198.51.100.20:3478?transport=tcp"],
        ["turns:198-51-100-20.edge.agora.io:443?transport=tcp"],
    ]
    assert {server.username for server in servers} == {"12345678"}


def test_a_browser_candidate_keeps_its_media_line() -> None:
    """HA's ``sdp_m_line_index`` is the library's ``sdp_mline_index``."""
    candidate = RTCIceCandidateInit(
        "candidate:1 1 udp 1 10.0.0.1 9 typ host", sdp_mid="0", sdp_m_line_index=0
    )

    assert to_ice_candidate(candidate) == IceCandidate(
        "candidate:1 1 udp 1 10.0.0.1 9 typ host", sdp_mid="0", sdp_mline_index=0
    )


def _token_coordinator(
    stream: StreamSubscriptionResponse | None = None,
) -> MagicMock:
    coordinator = MagicMock(spec=MammotionBaseUpdateCoordinator)
    coordinator.hass = MagicMock()
    coordinator.device = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.device_name = "Luba-VS00CLD"
    coordinator.streams_all_cameras = False
    coordinator._stream_data = None
    coordinator._stream_data_fetched_at = 0.0
    coordinator._agora_response = None
    coordinator.manager.get_stream_subscription = AsyncMock(
        return_value=Response(code=0, msg="ok", data=stream or make_stream_data())
    )
    return coordinator


class _UnreachableAPClient:
    """Stands in for the AP client; the conversion must fail before it is asked."""

    def __init__(self, **_kwargs: object) -> None:
        """Take the session the real client would."""

    async def choose_server(self, _creds: object) -> None:
        raise AssertionError("the AP was asked with credentials that cannot exist")


async def test_a_token_refresh_publishes_the_ap_ice_servers() -> None:
    """Entities read ``ice_servers`` and the camera joins with ``_agora_response``."""
    coordinator = _token_coordinator()
    ap = make_ap_response()

    with patch(
        "custom_components.mammotion.coordinator.async_choose_server",
        AsyncMock(return_value=ap),
    ):
        check = MammotionBaseUpdateCoordinator.async_check_stream_expiry
        stream, returned = await check(coordinator, force=True)

    assert stream == make_stream_data()
    assert returned is ap
    assert coordinator.ice_servers == to_rtc_ice_servers(ap)


async def test_a_rejected_ap_request_clears_the_ice_servers() -> None:
    """A stale TURN credential is worse than none: the browser would 401 on it."""
    coordinator = _token_coordinator()
    coordinator.ice_servers = to_rtc_ice_servers(make_ap_response())

    with patch(
        "custom_components.mammotion.coordinator.async_choose_server",
        AsyncMock(side_effect=APRejectedError({11: 1, 26: 1})),
    ):
        await MammotionBaseUpdateCoordinator.async_check_stream_expiry(
            coordinator, force=True
        )

    assert coordinator.ice_servers == []


@pytest.mark.regression
async def test_a_token_the_host_cannot_convert_clears_the_ice_servers() -> None:
    """A bad salt skipped ``except PyAgoraRTCError`` and left the old TURN credentials up."""
    coordinator = _token_coordinator(
        make_stream_data(openEncrypt=1, key="secret", salt="abcde")
    )
    coordinator.ice_servers = to_rtc_ice_servers(make_ap_response())

    with (
        patch(
            "custom_components.mammotion.stream_session.AgoraAPClient",
            _UnreachableAPClient,
        ),
        patch(
            "custom_components.mammotion.stream_session.async_get_clientsession",
            return_value=None,
        ),
    ):
        await MammotionBaseUpdateCoordinator.async_check_stream_expiry(
            coordinator, force=True
        )

    assert coordinator.ice_servers == []


@pytest.mark.regression
async def test_a_token_refresh_does_not_log_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The debug line printed the whole subscription response, channel token included."""
    coordinator = _token_coordinator()

    with (
        caplog.at_level(logging.DEBUG, logger="custom_components.mammotion"),
        patch(
            "custom_components.mammotion.coordinator.async_choose_server",
            AsyncMock(return_value=make_ap_response()),
        ),
    ):
        await MammotionBaseUpdateCoordinator.async_check_stream_expiry(
            coordinator, force=True
        )

    assert "channel-1" in caplog.text
    assert "channel-token" not in caplog.text
