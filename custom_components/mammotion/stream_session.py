"""Mammotion stream tokens and Home Assistant WebRTC types in pyagorartc's terms.

The mapping follows PyAgoraRTC's ``docs/migration.md`` §2.2 (credentials) and §1.2-1.3
(ICE servers and candidates).
"""

from __future__ import annotations

import base64

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyagorartc import (
    AgoraAPClient,
    APResponse,
    ChannelCredentials,
    ChannelEncryption,
    IceCandidate,
)
from pymammotion.http.model.camera_stream import StreamSubscriptionResponse
from webrtc_models import RTCIceCandidateInit, RTCIceServer


def mammotion_credentials(data: StreamSubscriptionResponse) -> ChannelCredentials:
    """Build the channel credentials a Mammotion stream token grants.

    ``availableTime`` is the session deadline and ``cameras`` the target uid, so
    neither is a credential.  ``areaCode`` stays at the library default: the cloud
    sends an Android SDK enum name (``AREA_CODE_EU``) the web AP may not accept.
    """
    encryption = None
    if data.openEncrypt and data.key:
        # pyagorartc models this but never sends it in the join and cannot decrypt
        # media, so an encrypted channel shows no picture (D20).
        encryption = ChannelEncryption(
            mode="aes-256-gcm2",
            secret=data.key,
            salt=base64.b64decode(data.salt) if data.salt else None,
        )
    return ChannelCredentials(
        app_id=data.appid,
        channel_name=data.channelName,
        token=data.token,
        uid=int(data.uid),
        license=data.license,
        encryption=encryption,
    )


async def async_choose_server(
    hass: HomeAssistant, data: StreamSubscriptionResponse
) -> APResponse:
    """Ask Agora's access points for gateway and TURN edges for this token."""
    client = AgoraAPClient(session=async_get_clientsession(hass))
    return await client.choose_server(mammotion_credentials(data))


def to_rtc_ice_servers(ap: APResponse) -> list[RTCIceServer]:
    """Return the first TURN edge's servers (udp, tcp, turns), as the SDK does."""
    return [
        RTCIceServer(
            urls=server.urls, username=server.username, credential=server.credential
        )
        for server in ap.get_ice_servers(use_all_turn_servers=False)
    ]


def to_ice_candidate(candidate: RTCIceCandidateInit) -> IceCandidate:
    """Convert a browser candidate to the session's candidate type."""
    return IceCandidate(
        candidate.candidate,
        sdp_mid=candidate.sdp_mid,
        sdp_mline_index=candidate.sdp_m_line_index,
    )
