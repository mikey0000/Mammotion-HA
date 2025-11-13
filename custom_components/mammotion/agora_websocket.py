"""Agora WebSocket handler for Mammotion WebRTC streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import ssl
import time
from dataclasses import dataclass
from typing import Any, Callable

import aiohttp
from homeassistant.core import HomeAssistant
from sdp_transform import parse as sdp_parse
from webrtc_models import RTCIceCandidateInit
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from .agora_api import AgoraResponse
from .agora_rtc_capabilities import (
    get_audio_codecs,
    get_audio_extensions,
    get_video_codecs_recv,
    get_video_codecs_sendrecv,
    get_video_extensions,
)
from .coordinator import StreamSubscriptionResponse

_LOGGER = logging.getLogger(__name__)


def _create_ws_ssl_context() -> ssl.SSLContext:
    """Create SSL context for WebSocket connections."""
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context


_SSL_CONTEXT = _create_ws_ssl_context()


@dataclass
class AddressEntry:
    """Agora edge server address entry."""

    ip: str
    port: int
    ticket: str


@dataclass
class ResponseInfo:
    """Agora API response information."""

    code: int
    addresses: list[AddressEntry]
    server_ts: int
    uid: int
    cid: int
    cname: str
    detail: dict[str, str]
    flag: int
    opid: int
    cert: str


@dataclass
class SdpInfo:
    """SDP parsing information."""

    parsed_sdp: dict
    fingerprint: str
    ice_ufrag: str
    ice_pwd: str
    audio_codecs: list[dict[str, Any]]
    video_codecs: list[dict[str, Any]]
    audio_extensions: list[dict[str, Any]]
    video_extensions: list[dict[str, Any]]
    audio_direction: str
    video_direction: str
    ice_candidates: list[dict[str, Any]]
    extmap_allow_mixed: bool
    setup_role: str


class AgoraWebSocketHandler:
    """Handle Agora WebSocket communications for WebRTC streaming."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the Agora WebSocket handler."""
        self.hass = hass
        self._websocket: ClientConnection | None = None
        self._connection_state = "DISCONNECTED"
        self._message_handlers: dict[str, Callable] = {}
        self._response_handlers: dict[str, asyncio.Future] = {}
        self.candidates: list[RTCIceCandidateInit] = []
        self._online_users: set[int] = set()
        self._video_streams: dict[int, dict[str, Any]] = {}
        self._answer_sdp: str | None = None
        self._setup_message_handlers()

    def _setup_message_handlers(self) -> None:
        """Set up message handlers for different WebSocket message types."""
        self._message_handlers = {
            "answer": self._handle_answer,
            "on_p2p_lost": self._handle_p2p_lost,
            "error": self._handle_error,
            "on_rtp_capability_change": self._handle_rtp_capability_change,
            "on_user_online": self._handle_user_online,
            "on_add_video_stream": self._handle_add_video_stream,
        }

    async def connect_and_join(
        self,
        agora_data: StreamSubscriptionResponse,
        offer_sdp: str,
        session_id: str,
        agora_response: AgoraResponse,
    ) -> str | None:
        """Connect to Agora WebSocket and perform join negotiation.

        Note: ICE candidates should be set in self.candidates before calling this method.
        These candidates will be incorporated into the offer SDP before sending to Agora.

        """
        _LOGGER.debug("Starting Agora WebSocket connection for session %s", session_id)
        _LOGGER.info("Agora data: %s", agora_data)
        # Get edge server information
        edge_info = await self._get_agora_edge_services(agora_data)
        if not edge_info:
            _LOGGER.error("Failed to get Agora edge services")
            return None

        _LOGGER.info("Edge: %s", edge_info)

        # Parse offer SDP for capabilities
        sdp_info = self._parse_offer_sdp(offer_sdp)
        if not sdp_info:
            _LOGGER.error("Failed to parse offer SDP")
            return None

        _LOGGER.info("Parsed offer SDP: %s", sdp_info)

        # Incorporate runtime ICE candidates into offer SDP
        if self.candidates:
            offer_sdp = self._add_candidates_to_sdp(offer_sdp, self.candidates)
            _LOGGER.info("Added %d candidates to offer SDP", len(self.candidates))
        _LOGGER.info("Offer SDP with candidates: %s", offer_sdp)
        # Try each edge address with timeout
        for edge_address in edge_info.addresses:
            edge_ip_dashed = edge_address.ip.replace(".", "-")
            ws_url = f"wss://{edge_ip_dashed}.edge.agora.io:{edge_address.port}"

            try:
                async with asyncio.timeout(10):  # 10 second timeout
                    async with connect(
                        ws_url, ssl=_SSL_CONTEXT, ping_timeout=30, close_timeout=30
                    ) as websocket:
                        self._websocket = websocket
                        self._connection_state = "CONNECTED"
                        _LOGGER.info("Connected to Agora WebSocket: %s", ws_url)

                        # Send join message
                        join_message = self._create_join_message(
                            agora_data, offer_sdp, edge_info, sdp_info, agora_response
                        )
                        await websocket.send(json.dumps(join_message))
                        _LOGGER.info("Sent join message to Agora %s", join_message)

                        # Handle responses
                        return await self._handle_websocket_messages(
                            websocket, session_id, sdp_info
                        )

            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "Connection timeout for edge address %s, trying next", ws_url
                )
                continue
            except (WebSocketException, json.JSONDecodeError) as ex:
                _LOGGER.warning("WebSocket connection failed for %s: %s", ws_url, ex)
                continue

        # If we get here, all connection attempts failed
        _LOGGER.error("Failed to connect to any Agora edge servers")
        self._connection_state = "DISCONNECTED"
        return None

    async def _handle_websocket_messages(
        self, websocket: ClientConnection, session_id: str, sdp_info: SdpInfo
    ) -> str | None:
        """Handle incoming WebSocket messages."""
        try:
            async for message in websocket:
                try:
                    response = json.loads(message)
                    _LOGGER.info("Received Agora message: %s", response)

                    message_type = response.get("_type")
                    message_id = response.get("_id")

                    # Handle responses to requests
                    if message_id and message_id in self._response_handlers:
                        future = self._response_handlers.pop(message_id)
                        if not future.done():
                            future.set_result(response)
                        continue

                    # Handle different message types
                    if message_type in self._message_handlers:
                        result = await self._message_handlers[message_type](response)
                        if result:
                            return result

                    # Check for successful join response
                    if response.get("_result") == "success":
                        return await self._handle_join_success(response, sdp_info)

                except json.JSONDecodeError as ex:
                    _LOGGER.error("Failed to parse Agora message: %s", ex)

        except WebSocketException as ex:
            _LOGGER.error("WebSocket communication error: %s", ex)
            self._connection_state = "DISCONNECTED"

        # Fallback: generate basic SDP if no proper response was received
        _LOGGER.warning(
            "No proper WebSocket response received, generating fallback SDP"
        )
        return self._generate_fallback_sdp()

    async def _handle_join_success(
        self, response: dict[str, Any], sdp_info: SdpInfo
    ) -> str | None:
        """Handle successful join response and generate answer SDP."""
        message = response.get("_message", {})
        ortc = message.get("ortc", {})

        if not ortc:
            _LOGGER.error("No ORTC parameters in join success response")
            _LOGGER.info("Full response message: %s", message)
            return None

        # Send set_client_role after successful connection
        await self._send_set_client_role(role="audience", level=1)

        _LOGGER.info("ORTC parameters: %s", ortc)

        # Generate answer SDP from ORTC parameters
        answer_sdp = self._generate_answer_sdp(ortc, sdp_info)
        if answer_sdp:
            _LOGGER.info("Generated answer SDP from Agora ORTC parameters")
            _LOGGER.info("Generated SDP: %s", answer_sdp)

            # Store answer SDP for later retrieval
            self._answer_sdp = answer_sdp

            return answer_sdp

        _LOGGER.error("Failed to generate answer SDP")
        return None

    async def _handle_answer(self, response: dict[str, Any]) -> str | None:
        """Handle answer message."""
        message = response.get("_message", {})
        sdp = message.get("sdp")
        if sdp:
            _LOGGER.info("Received direct answer SDP from Agora")
            return sdp
        return None

    async def _handle_p2p_lost(self, response: dict[str, Any]) -> None:
        """Handle P2P connection lost message."""
        error_code = response.get("error_code")
        error_str = response.get("error_str", "Unknown error")
        _LOGGER.warning("P2P connection lost: %s (code: %s)", error_str, error_code)

        # Handle specific error codes
        if error_code == 1 and "stun timeout" in error_str.lower():
            _LOGGER.info("STUN timeout detected, connection may need refreshing")
            # This could trigger a reconnection attempt

        self._connection_state = "DISCONNECTED"

    async def _handle_error(self, response: dict[str, Any]) -> None:
        """Handle error message."""
        message = response.get("_message", {})
        error = message.get("error", "Unknown error")
        _LOGGER.error("Agora WebSocket error: %s", error)

    async def _handle_rtp_capability_change(self, response: dict[str, Any]) -> None:
        """Handle RTP capability change notification."""
        message = response.get("_message", {})
        _LOGGER.info("RTP capabilities changed: %s", message)
        # Store capabilities if needed
        video_codecs = message.get("video_codec", [])
        extmap_allow_mixed = message.get("extmap_allow_mixed", False)
        web_av1_svc = message.get("web_av1_svc", False)
        _LOGGER.info(
            "Video codecs: %s, extmap_allow_mixed: %s, web_av1_svc: %s",
            video_codecs,
            extmap_allow_mixed,
            web_av1_svc,
        )

    async def _handle_user_online(self, response: dict[str, Any]) -> None:
        """Handle user online notification."""
        message = response.get("_message", {})
        uid = message.get("uid")
        if uid:
            self._online_users.add(uid)
            _LOGGER.info("User %s came online", uid)

    async def _handle_add_video_stream(self, response: dict[str, Any]) -> None:
        """Handle add video stream notification and auto-subscribe."""
        message = response.get("_message", {})
        uid = message.get("uid")
        ssrc_id = message.get("ssrcId")
        rtx_ssrc_id = message.get("rtxSsrcId")
        cname = message.get("cname")
        is_video = message.get("video", False)

        if uid and is_video:
            _LOGGER.info(
                "Video stream added - uid: %s, ssrcId: %s, rtxSsrcId: %s, cname: %s",
                uid,
                ssrc_id,
                rtx_ssrc_id,
                cname,
            )

            # Store stream info
            self._video_streams[uid] = {
                "ssrcId": ssrc_id,
                "rtxSsrcId": rtx_ssrc_id,
                "cname": cname,
            }

            # Auto-subscribe to the video stream
            if self._websocket:
                await self._send_subscribe(stream_id=uid, ssrc_id=ssrc_id, codec="vp8")

    def _create_join_message(
        self,
        agora_data: StreamSubscriptionResponse,
        offer_sdp: str,
        edge_info: ResponseInfo,
        sdp_info: SdpInfo,
        agora_response: AgoraResponse,
    ) -> dict[str, Any]:
        """Create join_v3 message for Agora WebSocket."""
        message_id = secrets.token_hex(3)  # 6 characters
        process_id = f"process-{secrets.token_hex(4)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(6)}"

        return {
            "_id": message_id,
            "_type": "join_v3",
            "_message": {
                "p2p_id": 3,
                "session_id": secrets.token_hex(16).upper(),
                "app_id": agora_data.appid,
                "channel_key": agora_data.token,
                "channel_name": agora_data.channelName,
                "sdk_version": "4.24.0",
                "browser": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
                "process_id": process_id,
                "mode": "live",
                "codec": "vp8",
                "role": "audience",
                "has_changed_gateway": False,
                "ap_response": agora_response.to_ap_response(),
                "extend": "",
                "details": {},
                "features": {"rejoin": True},
                "attributes": {
                    "userAttributes": {
                        "enableAudioMetadata": False,
                        "enableAudioPts": False,
                        "enablePublishedUserList": True,
                        "maxSubscription": 50,
                        "enableUserLicenseCheck": True,
                        "enableRTX": True,
                        "enableInstantVideo": False,
                        "enableDataStream2": False,
                        "enableAutFeedback": True,
                        "enableUserAutoRebalanceCheck": True,
                        "enableXR": True,
                        "enableLossbasedBwe": True,
                        "enableAutCC": True,
                        "enablePreallocPC": False,
                        "enablePubTWCC": False,
                        "enableSubTWCC": True,
                        "enablePubRTX": True,
                        "enableSubRTX": True,
                    }
                },
                "join_ts": int(time.time() * 1000),
                "ortc": {
                    "iceParameters": {
                        "iceUfrag": sdp_info.ice_ufrag,
                        "icePwd": sdp_info.ice_pwd,
                    },
                    "dtlsParameters": {
                        "fingerprints": [
                            {
                                "hashFunction": "sha-256",
                                "fingerprint": sdp_info.fingerprint,
                            }
                        ]
                    },
                    "rtpCapabilities": {
                        "send": {
                            "audioCodecs": [],
                            "audioExtensions": [],
                            "videoCodecs": [],
                            "videoExtensions": [],
                        },
                        "recv": {
                            "audioCodecs": [],
                            "audioExtensions": [],
                            "videoCodecs": get_video_codecs_recv(),
                            "videoExtensions": [],
                        },
                        "sendrecv": {
                            "audioCodecs": get_audio_codecs(),
                            "audioExtensions": get_audio_extensions(),
                            "videoCodecs": get_video_codecs_sendrecv(),
                            "videoExtensions": get_video_extensions(),
                        },
                    },
                    "version": "2",
                },
            },
        }

    async def _send_set_client_role(
        self, role: str = "audience", level: int = 1
    ) -> None:
        """Send set_client_role message to Agora.

        Args:
            role: Client role - "audience" or "host"
            level: Role level (default: 1)

        """
        if not self._websocket:
            _LOGGER.error("Cannot send set_client_role: websocket not connected")
            return

        message_id = secrets.token_hex(3)
        message = {
            "_id": message_id,
            "_type": "set_client_role",
            "_message": {
                "role": role,
                "level": level,
                "client_ts": int(time.time() * 1000),
            },
        }

        _LOGGER.info("Sending set_client_role message: %s", message)
        await self._websocket.send(json.dumps(message))

    async def _send_subscribe(
        self,
        stream_id: int,
        ssrc_id: int,
        codec: str = "vp8",
        stream_type: str = "video",
        mode: str = "live",
        p2p_id: int = 4,
        twcc: bool = True,
        rtx: bool = True,
        extend: str = "",
    ) -> None:
        """Send subscribe message to Agora.

        Args:
            stream_id: Stream ID (usually the uid)
            ssrc_id: SSRC ID from on_add_video_stream
            codec: Video codec (default: "vp8")
            stream_type: Stream type (default: "video")
            mode: Mode (default: "live")
            p2p_id: P2P ID (default: 4)
            twcc: Enable transport-wide congestion control
            rtx: Enable retransmission
            extend: Extended info

        """
        if not self._websocket:
            _LOGGER.error("Cannot send subscribe: websocket not connected")
            return

        message_id = secrets.token_hex(3)
        message = {
            "_id": message_id,
            "_type": "subscribe",
            "_message": {
                "stream_id": stream_id,
                "stream_type": stream_type,
                "mode": mode,
                "codec": codec,
                "p2p_id": p2p_id,
                "twcc": twcc,
                "rtx": rtx,
                "extend": extend,
                "ssrcId": ssrc_id,
            },
        }

        _LOGGER.info("Sending subscribe message: %s", message)
        await self._websocket.send(json.dumps(message))

    @staticmethod
    def _add_candidates_to_sdp(sdp: str, candidates: list[RTCIceCandidateInit]) -> str:
        """Add ICE candidates to SDP.

        Args:
            sdp: Original SDP string
            candidates: List of ICE candidates (dict or string format)

        Returns:
            Modified SDP with candidates added

        """
        sdp_lines = sdp.split("\n")
        result_lines = []
        in_media_section = False

        for i, line in enumerate(sdp_lines):
            # Check if we're entering a media section
            if line.startswith("m="):
                in_media_section = True

            # Check if we're at the end of a media section (next m= line or end of SDP)
            next_is_media = i + 1 < len(sdp_lines) and sdp_lines[i + 1].startswith("m=")
            is_last_line = i == len(sdp_lines) - 1

            result_lines.append(line)

            # Add candidates at the end of each media section
            if in_media_section and (next_is_media or is_last_line):
                for cand in candidates:
                    cand_str = cand.candidate

                    if not cand_str:
                        continue

                    # Normalize to proper format (ensure it starts with "a=candidate:")
                    if not cand_str.startswith("a="):
                        if cand_str.startswith("candidate:"):
                            cand_str = "a=" + cand_str
                        else:
                            cand_str = "a=candidate:" + cand_str

                    result_lines.append(cand_str)

        return "\n".join(result_lines)

    @staticmethod
    def _parse_offer_sdp(offer_sdp: str) -> SdpInfo | None:
        """Parse offer SDP to extract capabilities and parameters using sdp_transform."""
        try:
            # Parse SDP using sdp_transform
            parsed_sdp = sdp_parse(offer_sdp)
            _LOGGER.info("Parsed SDP structure: %s", parsed_sdp)

            # Extract fingerprint
            fingerprint = ""
            if "fingerprint" in parsed_sdp:
                fingerprint = parsed_sdp["fingerprint"]["hash"]
            else:
                # Check in media sections
                for media in parsed_sdp.get("media", []):
                    if "fingerprint" in media:
                        fingerprint = media["fingerprint"]["hash"]
                        break

            # Extract ICE parameters
            ice_ufrag = parsed_sdp.get("iceUfrag", "")
            ice_pwd = parsed_sdp.get("icePwd", "")

            # Check in media sections if not found at top level
            if not ice_ufrag or not ice_pwd:
                for media in parsed_sdp.get("media", []):
                    if not ice_ufrag and "iceUfrag" in media:
                        ice_ufrag = media["iceUfrag"]
                    if not ice_pwd and "icePwd" in media:
                        ice_pwd = media["icePwd"]
                    if ice_ufrag and ice_pwd:
                        break

            audio_codecs = []
            video_codecs = []
            audio_extensions = []
            video_extensions = []

            audio_direction = "sendrecv"
            video_direction = "sendrecv"

            # Process media sections
            for media in parsed_sdp.get("media", []):
                media_type = media.get("type")

                # capture direction per media so answer generator can choose complementary dir
                dir_val = media.get("direction", "sendrecv")

                if media_type == "audio":
                    audio_direction = dir_val
                elif media_type == "video":
                    video_direction = dir_val

                # Process RTP codecs
                for rtp in media.get("rtp", []):
                    codec_entry = {
                        "payloadType": rtp["payload"],
                        "rtpMap": {
                            "encodingName": rtp["codec"],
                            "clockRate": rtp["rate"],
                        },
                        "rtcpFeedbacks": [],
                    }

                    # Add encoding parameters if present
                    if "encoding" in rtp:
                        codec_entry["rtpMap"]["encodingParameters"] = rtp["encoding"]

                    # Find fmtp parameters for this payload type
                    fmtp_params = {}
                    for fmtp in media.get("fmtp", []):
                        if fmtp["payload"] == rtp["payload"]:
                            # Parse config string into parameters
                            config = fmtp.get("config", "")
                            if config:
                                params = {}
                                for param_pair in config.split(";"):
                                    if "=" in param_pair:
                                        key, value = param_pair.split("=", 1)
                                        params[key.strip()] = value.strip()
                                    else:
                                        # Handle cases like "111/111" for RED codec
                                        params[param_pair.strip()] = None
                                if params:
                                    fmtp_params = params
                            break

                    # Add fmtp if found
                    if fmtp_params:
                        codec_entry["fmtp"] = {"parameters": fmtp_params}

                    # Process RTCP feedback from SDP
                    rtcp_feedbacks = []
                    for rtcp_fb in media.get("rtcpFb", []):
                        if rtcp_fb.get("payload") == rtp["payload"]:
                            feedback = {"type": rtcp_fb["type"]}
                            if "subtype" in rtcp_fb:
                                feedback["parameter"] = rtcp_fb["subtype"]
                            rtcp_feedbacks.append(feedback)

                    # Add default RTCP feedback based on media type and codec if none found
                    if not rtcp_feedbacks:
                        codec_name = rtp["codec"].upper()
                        if media_type == "video":
                            if codec_name in ["VP8", "VP9", "H264", "AV1"]:
                                rtcp_feedbacks = [
                                    {"type": "goog-remb"},
                                    {"type": "transport-cc"},
                                    {"type": "ccm", "parameter": "fir"},
                                    {"type": "nack"},
                                    {"type": "nack", "parameter": "pli"},
                                    {"type": "rrtr"},
                                ]
                            elif codec_name == "RTX":
                                rtcp_feedbacks = [{"type": "rrtr"}]
                            else:
                                rtcp_feedbacks = [{"type": "rrtr"}]
                        elif media_type == "audio":
                            rtcp_feedbacks = [{"type": "rrtr"}]
                            if codec_name == "OPUS":
                                rtcp_feedbacks.append({"type": "transport-cc"})

                    codec_entry["rtcpFeedbacks"] = rtcp_feedbacks

                    if media_type == "video":
                        video_codecs.append(codec_entry)
                    elif media_type == "audio":
                        audio_codecs.append(codec_entry)

                # Process extensions
                for ext in media.get("ext", []):
                    ext_entry = {
                        "entry": ext["value"],
                        "extensionName": ext["uri"],
                    }

                    # Map common extension names to match Agora's format
                    uri_mappings = {
                        "urn:ietf:params:rtp-hdrext:ssrc-audio-level": "urn:ietf:params:rtp-hdrext:ssrc-audio-level",
                        "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time": "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
                        "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01": "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
                        "urn:ietf:params:rtp-hdrext:sdes:mid": "urn:ietf:params:rtp-hdrext:sdes:mid",
                        "urn:ietf:params:rtp-hdrext:toffset": "urn:ietf:params:rtp-hdrext:toffset",
                        "urn:3gpp:video-orientation": "urn:3gpp:video-orientation",
                        "http://www.webrtc.org/experiments/rtp-hdrext/playout-delay": "http://www.webrtc.org/experiments/rtp-hdrext/playout-delay",
                        "http://www.webrtc.org/experiments/rtp-hdrext/video-content-type": "http://www.webrtc.org/experiments/rtp-hdrext/video-content-type",
                        "http://www.webrtc.org/experiments/rtp-hdrext/video-timing": "http://www.webrtc.org/experiments/rtp-hdrext/video-timing",
                        "http://www.webrtc.org/experiments/rtp-hdrext/color-space": "http://www.webrtc.org/experiments/rtp-hdrext/color-space",
                        "urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id": "urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id",
                        "urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id": "urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id",
                    }

                    # Use mapped URI if available
                    ext_entry["extensionName"] = uri_mappings.get(
                        ext["uri"], ext["uri"]
                    )

                    if media_type == "audio":
                        audio_extensions.append(ext_entry)
                    elif media_type == "video":
                        video_extensions.append(ext_entry)

            # Extract ICE candidates from SDP
            ice_candidates = []
            for media in parsed_sdp.get("media", []):
                for candidate in media.get("candidates", []):
                    # Convert sdp_transform candidate format to ORTC format
                    ice_candidate = {
                        "foundation": candidate.get("foundation", ""),
                        "protocol": candidate.get("transport", "udp"),
                        "priority": candidate.get("priority", 0),
                        "ip": candidate.get("ip", ""),
                        "port": candidate.get("port", 0),
                        "type": candidate.get("type", "host"),
                    }
                    # Add optional fields if present
                    if "generation" in candidate:
                        ice_candidate["generation"] = candidate["generation"]
                    if "raddr" in candidate:
                        ice_candidate["relatedAddress"] = candidate["raddr"]
                    if "rport" in candidate:
                        ice_candidate["relatedPort"] = candidate["rport"]
                    if "tcptype" in candidate:
                        ice_candidate["tcpType"] = candidate["tcptype"]

                    ice_candidates.append(ice_candidate)

            # Extract extmap-allow-mixed from session level
            extmap_allow_mixed = parsed_sdp.get("extmapAllowMixed", False)

            # Extract setup role from first media section (usually same for all)
            setup_role = "actpass"  # default
            for media in parsed_sdp.get("media", []):
                if "setup" in media:
                    setup_role = media["setup"]
                    break

            return SdpInfo(
                parsed_sdp,
                fingerprint=fingerprint,
                ice_ufrag=ice_ufrag,
                ice_pwd=ice_pwd,
                audio_codecs=audio_codecs,
                video_codecs=video_codecs,
                audio_extensions=audio_extensions,
                video_extensions=video_extensions,
                audio_direction=audio_direction,
                video_direction=video_direction,
                ice_candidates=ice_candidates,
                extmap_allow_mixed=extmap_allow_mixed,
                setup_role=setup_role,
            )

        except (ValueError, IndexError, KeyError) as ex:
            _LOGGER.error("Failed to parse offer SDP with sdp_transform: %s", ex)
            return None

    def _generate_answer_sdp(
        self, ortc: dict[str, Any], sdp_info: SdpInfo
    ) -> str | None:
        """Generate SDP answer from ORTC parameters."""
        try:
            import random
            import secrets
            from collections import defaultdict

            ice_params = ortc.get("iceParameters", {})
            dtls_params = ortc.get("dtlsParameters", {})
            rtp_caps = ortc.get("rtpCapabilities", {}).get("sendrecv", {})

            _LOGGER.debug("ICE params: %s", ice_params)
            _LOGGER.debug("DTLS params: %s", dtls_params)
            _LOGGER.debug("RTP caps: %s", rtp_caps)

            # Extract ICE candidates and credentials from ORTC
            ortc_candidates = ice_params.get("candidates", []) or []
            ice_ufrag = ice_params.get("iceUfrag", "") or ""
            ice_pwd = ice_params.get("icePwd", "") or ""

            # fallback credentials
            if not ice_ufrag:
                ice_ufrag = secrets.token_hex(4)
                _LOGGER.warning("Using fallback ICE ufrag: %s", ice_ufrag)
            if not ice_pwd:
                ice_pwd = secrets.token_hex(16)
                _LOGGER.warning("Using fallback ICE pwd")

            # Extract DTLS fingerprint
            fingerprints = dtls_params.get("fingerprints", []) or []
            fingerprint = ""
            if fingerprints:
                fp = fingerprints[0]
                fingerprint = (
                    f"{fp.get('algorithm', 'sha-256')} {fp.get('fingerprint', '')}"
                )
            if not fingerprint:
                fallback_fingerprint = ":".join(
                    [secrets.token_hex(1).upper() for _ in range(32)]
                )
                fingerprint = f"sha-256 {fallback_fingerprint}"
                _LOGGER.warning("Using fallback fingerprint")

            # Build candidate lines grouped by mid. '*' = generic (no mid)
            candidates_by_mid = defaultdict(list)

            # Add ORTC-provided candidates from server response
            for i, c in enumerate(ortc_candidates):
                foundation = c.get("foundation", f"candidate{i}")
                protocol = c.get("protocol", "udp")
                priority = c.get("priority", 2103266323)
                ip = c.get("ip", "")
                port = c.get("port", 0)
                ctype = c.get("type", "host")
                cand_line = f"a=candidate:{foundation} 1 {protocol} {priority} {ip} {port} typ {ctype}"
                if c.get("generation") is not None:
                    cand_line += f" generation {c.get('generation')}"
                candidates_by_mid["*"].append(cand_line)

            # Extract codec lists and extensions from ORTC
            video_codecs = rtp_caps.get("videoCodecs", []) or []
            audio_codecs = rtp_caps.get("audioCodecs", []) or []
            video_extensions = rtp_caps.get("videoExtensions", []) or []
            audio_extensions = rtp_caps.get("audioExtensions", []) or []

            def answer_direction_for_offer(offer_dir: str) -> str:
                if offer_dir == "sendonly":
                    return "recvonly"
                if offer_dir == "recvonly":
                    return "sendonly"
                if offer_dir == "sendrecv":
                    return "sendrecv"
                return "inactive"

            # Parse media from offer - MUST preserve exact order for answer
            media = sdp_info.parsed_sdp.get("media", []) or []

            # Build BUNDLE list from offer
            bundle_group = sdp_info.parsed_sdp.get("groups", [{}])[0] if sdp_info.parsed_sdp.get("groups") else {}
            bundle_mids = bundle_group.get("mids", "0 1") if bundle_group else "0 1"

            # Determine answer setup role based on offer
            def answer_setup_for_offer(offer_setup: str) -> str:
                if offer_setup == "actpass":
                    return "active"  # We choose to be active
                if offer_setup == "active":
                    return "passive"  # Must be passive
                if offer_setup == "passive":
                    return "active"  # Must be active
                return "active"  # default

            answer_setup = answer_setup_for_offer(sdp_info.setup_role)

            # build base sdp header
            sdp_lines = [
                "v=0",
                f"o=- {sdp_info.parsed_sdp['origin']['sessionId']} {sdp_info.parsed_sdp['origin']['sessionVersion']} IN IP4 127.0.0.1",
                "s=-",
                "t=0 0",
                f"a=group:BUNDLE {bundle_mids}",
            ]

            # Add extmap-allow-mixed if present in offer
            if sdp_info.extmap_allow_mixed:
                sdp_lines.append("a=extmap-allow-mixed")

            sdp_lines.append("a=msid-semantic: WMS")

            # Generate media sections in EXACT SAME ORDER as offer
            # The answer MUST match the offer's m-line order
            for idx, m in enumerate(media):
                mtype = m.get("type", "audio")
                offer_dir = m.get("direction", "sendonly")
                answer_dir = answer_direction_for_offer(offer_dir)
                mid = str(m.get("mid", str(idx)))

                # Select codecs for this media type
                if mtype == "audio":
                    codecs = audio_codecs
                    extensions = audio_extensions
                else:
                    codecs = video_codecs
                    extensions = video_extensions

                # Build payload type list
                payload_types = [str(codec.get("payloadType")) for codec in codecs]
                payloads_str = " ".join(payload_types)

                # media header
                sdp_lines.append(f"m={mtype} 9 UDP/TLS/RTP/SAVPF {payloads_str}")
                sdp_lines.append("c=IN IP4 0.0.0.0")
                sdp_lines.append("a=rtcp:9 IN IP4 0.0.0.0")
                sdp_lines.append(f"a=ice-ufrag:{ice_ufrag}")
                sdp_lines.append(f"a=ice-pwd:{ice_pwd}")
                sdp_lines.append("a=ice-options:trickle")
                sdp_lines.append(f"a=fingerprint:{fingerprint}")
                sdp_lines.append(f"a=setup:{answer_setup}")
                sdp_lines.append(f"a=mid:{mid}")

                # Add RTP extensions - MUST use offer's extension IDs
                # Build mapping from offer's extension URIs to their IDs
                offer_ext_map = {}
                if mtype == "audio":
                    for ext in sdp_info.audio_extensions:
                        offer_ext_map[ext.get("extensionName")] = ext.get("entry")
                else:
                    for ext in sdp_info.video_extensions:
                        offer_ext_map[ext.get("extensionName")] = ext.get("entry")

                # Add extensions using offer's IDs for matching URIs
                for ext in extensions:
                    ext_name = ext.get("extensionName")
                    if not ext_name:
                        continue

                    # Use the offer's extension ID if this extension was in the offer
                    if ext_name in offer_ext_map:
                        entry = offer_ext_map[ext_name]
                        sdp_lines.append(f"a=extmap:{entry} {ext_name}")
                    # Otherwise skip this extension (not negotiated in offer)

                sdp_lines.append(f"a={answer_dir}")
                sdp_lines.append("a=rtcp-mux")
                sdp_lines.append("a=rtcp-rsize")

                # Add codec details (rtpmap, rtcp-fb, fmtp)
                for codec in codecs:
                    pt = codec.get("payloadType")
                    rtp_map = codec.get("rtpMap", {})
                    encoding_name = rtp_map.get("encodingName", "")
                    clock_rate = rtp_map.get("clockRate", 90000)
                    encoding_params = rtp_map.get("encodingParameters")

                    # rtpmap line
                    if encoding_params:
                        sdp_lines.append(
                            f"a=rtpmap:{pt} {encoding_name}/{clock_rate}/{encoding_params}"
                        )
                    else:
                        sdp_lines.append(f"a=rtpmap:{pt} {encoding_name}/{clock_rate}")

                    # rtcp-fb lines
                    for fb in codec.get("rtcpFeedbacks", []):
                        fb_type = fb.get("type")
                        fb_param = fb.get("parameter")
                        if fb_param:
                            sdp_lines.append(f"a=rtcp-fb:{pt} {fb_type} {fb_param}")
                        else:
                            sdp_lines.append(f"a=rtcp-fb:{pt} {fb_type}")

                    # fmtp line
                    fmtp = codec.get("fmtp", {})
                    if fmtp:
                        params = fmtp.get("parameters", {})
                        if params:
                            param_str = ";".join(
                                [f"{k}={v}" for k, v in params.items()]
                            )
                            sdp_lines.append(f"a=fmtp:{pt} {param_str}")

                # send SSRC if answer sends
                if "send" in answer_dir:
                    local_ssrc = random.randint(1, 2**32 - 1)
                    local_cname = ortc.get("cname") or f"pc-{secrets.token_hex(6)}"
                    sdp_lines.append(f"a=ssrc:{local_ssrc} cname:{local_cname}")

                # append candidates for this media: specific mid then generic ones
                # try exact mid key, also accept numeric mline index as key
                specific = candidates_by_mid.get(mid, []) + candidates_by_mid.get(
                    str(idx), []
                )
                for cl in specific:
                    sdp_lines.append(cl)
                for cl in candidates_by_mid.get("*", []):
                    sdp_lines.append(cl)

            generated_sdp = "\r\n".join(sdp_lines) + "\r\n"
            _LOGGER.info("Generated SDP lines count: %s", len(sdp_lines))
            _LOGGER.debug("Generated SDP content: %s", generated_sdp)

            if self._validate_sdp(generated_sdp):
                return generated_sdp
            _LOGGER.error("Generated SDP failed validation")
            return None

        except (KeyError, ValueError, AttributeError) as ex:
            _LOGGER.error("Failed to generate answer SDP: %s", ex)
            return None

    def _validate_sdp(self, sdp: str) -> bool:
        """Validate SDP format to ensure it's parseable by WebRTC."""
        if not sdp or len(sdp.strip()) == 0:
            _LOGGER.error("SDP is empty")
            return False

        lines = sdp.split("\r\n")
        has_version = False
        has_origin = False
        has_session_name = False
        has_timing = False
        m_line_count = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("v="):
                has_version = True
            elif line.startswith("o="):
                has_origin = True
            elif line.startswith("s="):
                has_session_name = True
            elif line.startswith("t="):
                has_timing = True
            elif line.startswith("m="):
                m_line_count += 1

        if not has_version:
            _LOGGER.error("SDP missing version line (v=)")
            return False
        if not has_origin:
            _LOGGER.error("SDP missing origin line (o=)")
            return False
        if not has_session_name:
            _LOGGER.error("SDP missing session name line (s=)")
            return False
        if not has_timing:
            _LOGGER.error("SDP missing timing line (t=)")
            return False
        if m_line_count < 2:
            _LOGGER.error(
                "SDP has %s m-lines, expected 2 (audio + video)", m_line_count
            )
            return False

        _LOGGER.debug("SDP validation passed: %s m-lines found", m_line_count)
        return True

    def _generate_fallback_sdp(self) -> str:
        """Generate a basic fallback SDP answer."""
        _LOGGER.info("Generating fallback SDP with default parameters")

        # Generate basic parameters
        ice_ufrag = secrets.token_hex(4)
        ice_pwd = secrets.token_hex(16)
        fallback_fingerprint = ":".join(
            [secrets.token_hex(1).upper() for _ in range(32)]
        )
        fingerprint = f"sha-256 {fallback_fingerprint}"

        # Default codec payload types
        opus_payload = 109
        vp8_payload = 120

        # Build fallback SDP answer
        sdp_lines = [
            "v=0",
            f"o=- {secrets.randbelow(2**63)} 2 IN IP4 127.0.0.1",
            "s=-",
            "t=0 0",
            "a=group:BUNDLE 0 1",
            "a=msid-semantic: WMS",
            "",
            # Audio m-line
            f"m=audio 9 UDP/TLS/RTP/SAVPF {opus_payload}",
            "c=IN IP4 0.0.0.0",
            "a=rtcp:9 IN IP4 0.0.0.0",
            f"a=ice-ufrag:{ice_ufrag}",
            f"a=ice-pwd:{ice_pwd}",
            "a=ice-options:trickle",
            f"a=fingerprint:{fingerprint}",
            "a=setup:active",
            "a=mid:0",
            "a=sendrecv",
            "a=rtcp-mux",
            f"a=rtpmap:{opus_payload} opus/48000/2",
            "",
            # Video m-line
            f"m=video 9 UDP/TLS/RTP/SAVPF {vp8_payload}",
            "c=IN IP4 0.0.0.0",
            "a=rtcp:9 IN IP4 0.0.0.0",
            f"a=ice-ufrag:{ice_ufrag}",
            f"a=ice-pwd:{ice_pwd}",
            "a=ice-options:trickle",
            f"a=fingerprint:{fingerprint}",
            "a=setup:active",
            "a=mid:1",
            "a=sendrecv",
            "a=rtcp-mux",
            f"a=rtpmap:{vp8_payload} VP8/90000",
        ]

        generated_sdp = "\r\n".join(sdp_lines) + "\r\n"
        _LOGGER.debug("Generated fallback SDP: %s", generated_sdp)

        # Validate fallback SDP
        if self._validate_sdp(generated_sdp):
            return generated_sdp
        _LOGGER.error("Fallback SDP failed validation")
        # Return a minimal valid SDP as last resort
        return self._generate_minimal_sdp()

    def _generate_minimal_sdp(self) -> str:
        """Generate minimal valid SDP as last resort."""
        _LOGGER.warning("Generating minimal SDP as last resort")

        ice_ufrag = secrets.token_hex(4)
        ice_pwd = secrets.token_hex(16)

        minimal_sdp = (
            "v=0\r\n"
            f"o=- {secrets.randbelow(2**63)} 2 IN IP4 127.0.0.1\r\n"
            "s=-\r\n"
            "t=0 0\r\n"
            "a=group:BUNDLE 0 1\r\n"
            "a=msid-semantic: WMS\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 109\r\n"
            "c=IN IP4 0.0.0.0\r\n"
            f"a=ice-ufrag:{ice_ufrag}\r\n"
            f"a=ice-pwd:{ice_pwd}\r\n"
            "a=fingerprint:sha-256 00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00\r\n"
            "a=setup:active\r\n"
            "a=mid:0\r\n"
            "a=sendrecv\r\n"
            "a=rtcp-mux\r\n"
            "a=rtpmap:109 opus/48000/2\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 120\r\n"
            "c=IN IP4 0.0.0.0\r\n"
            f"a=ice-ufrag:{ice_ufrag}\r\n"
            f"a=ice-pwd:{ice_pwd}\r\n"
            "a=fingerprint:sha-256 00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00\r\n"
            "a=setup:active\r\n"
            "a=mid:1\r\n"
            "a=sendrecv\r\n"
            "a=rtcp-mux\r\n"
            "a=rtpmap:120 VP8/90000\r\n"
        )

        return minimal_sdp

    async def _get_agora_edge_services(
        self, agora_data: StreamSubscriptionResponse
    ) -> ResponseInfo | None:
        """Get Agora edge services information."""
        app_id = agora_data.appid
        channel_name = agora_data.channelName
        token = agora_data.token
        uid = int(agora_data.uid)

        # Generate required IDs for the API call
        client_ts = int(time.time() * 1000)
        opid = secrets.randbelow(2**31)
        sid = secrets.token_hex(16).upper()

        # Create the request payload
        request_payload = {
            "appid": app_id,
            "client_ts": client_ts,
            "opid": opid,
            "sid": sid,
            "request_bodies": [
                {
                    "uri": 22,
                    "buffer": {
                        "cname": channel_name,
                        "detail": {"11": "CN,GLOBAL", "17": "1", "22": "CN,GLOBAL"},
                        "key": token,
                        "service_ids": [11, 26],
                        "uid": uid,
                    },
                }
            ],
        }

        # Create multipart form data using aiohttp.MultipartWriter
        writer = aiohttp.MultipartWriter("form-data")
        part = writer.append(json.dumps(request_payload))
        part.set_content_disposition("form-data", name="request")

        headers = {
            "User-Agent": "Home Assistant WebRTC",
        }

        api_url = "https://webrtc2-ap-web-1.agora.io/api/v2/transpond/webrtc?v=2"

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    api_url,
                    data=writer,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response,
            ):
                if response.status != 200:
                    _LOGGER.error("Agora API returned status %s", response.status)
                    raise aiohttp.ClientError(f"API returned status {response.status}")

                # Read response as JSON
                response_text = await response.text()
                _LOGGER.debug("Agora API raw response: %s", response_text)

                response_data = json.loads(response_text)
                _LOGGER.debug("Agora API parsed response: %s", response_data)

                # Extract edge services from response
                response_bodies = response_data.get("response_body", [])
                for body in reversed(response_bodies):
                    buffer = body.get("buffer", {})
                    if buffer and buffer.get("flag") == 4096:
                        edges_services = buffer.get("edges_services", [])
                        if edges_services:
                            return ResponseInfo(
                                code=buffer["code"],
                                addresses=[
                                    AddressEntry(
                                        ip=es["ip"],
                                        port=es["port"],
                                        ticket=buffer["cert"],
                                    )
                                    for es in edges_services
                                ],
                                server_ts=response_data["enter_ts"],
                                uid=buffer["uid"],
                                cid=buffer["cid"],
                                cname=buffer["cname"],
                                detail={
                                    **buffer.get("detail", {}),
                                    **response_data.get("detail", {}),
                                },
                                flag=buffer["flag"],
                                opid=response_data["opid"],
                                cert=buffer["cert"],
                            )

                # Fallback if no edge services found
                _LOGGER.warning(
                    "No edge services found in Agora API response, using fallback"
                )
                raise aiohttp.ClientError("No edge services available")

        except (aiohttp.ClientError, json.JSONDecodeError) as ex:
            _LOGGER.error("Failed to get Agora edge services: %s", ex)
            return None

    @property
    def is_connected(self) -> bool:
        """Return whether WebSocket is connected."""
        return self._connection_state == "CONNECTED"

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        if self._websocket:
            await self._websocket.close()
            self._websocket = None
        self._connection_state = "DISCONNECTED"

    def add_ice_candidate(self, candidate: RTCIceCandidateInit):
        self.candidates.append(candidate)
