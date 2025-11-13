"""Agora WebRTC API client for server-to-client communication.

This module provides a client for interacting with the Agora WebRTC API endpoint
at `/api/v2/transpond/webrtc?v=2`. It handles request construction, API calls,
and response parsing to get edge server addresses and tickets for WebRTC connections.

The implementation is based on analysis of the Agora JavaScript SDK (agoraRTC_N.js)
to ensure compatibility and parity with client-side behavior.
"""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from random import randint
from typing import Optional

import aiohttp

# Service flags (from Agora SDK)
SERVICE_FLAGS = {
    "CHOOSE_SERVER": 11,
    "CLOUD_PROXY": 18,
    "CLOUD_PROXY_5": 20,
    "CLOUD_PROXY_FALLBACK": 26,
}


def derive_password(uid: int | str) -> str:
    """Derive TURN/STUN password using SHA-256 hash.

    Python equivalent of the JavaScript Ww function from agoraRTC_N.js:
    const Ww = async e => digest("SHA-256", jw(e)).hex()

    This is used when ENCRYPT_PROXY_USERNAME_AND_PSW feature flag is enabled.

    Args:
        uid: User ID (numeric or string)

    Returns:
        Hexadecimal string representation of SHA-256 hash

    """
    uid_str = str(uid)
    return hashlib.sha256(uid_str.encode("utf-8")).hexdigest()


@dataclass
class EdgeAddress:
    """Represents an edge server address."""

    ip: str
    port: int
    username: Optional[str] = None
    credentials: Optional[str] = None
    ticket: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        result = {"ip": self.ip, "port": self.port}
        if self.ticket:
            result["ticket"] = self.ticket
        return result


@dataclass
class ICEServer:
    """Represents an RTCIceServer configuration."""

    urls: str
    username: Optional[str] = None
    credential: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for RTCPeerConnection."""
        result = {"urls": self.urls}
        if self.username:
            result["username"] = self.username
        if self.credential:
            result["credential"] = self.credential
        return result


@dataclass
class AgoraResponse:
    """Parsed response from Agora WebRTC API.

    When requesting multiple service flags, contains separate entries for
    each flag in the responses dict.
    """

    code: int
    addresses: list[EdgeAddress]
    ticket: str
    uid: int
    cid: int
    cname: str
    server_ts: int
    detail: dict
    flag: int
    opid: int
    responses: dict = None  # Multi-flag responses: {flag: response_dict}

    @classmethod
    def from_api_response(cls, response_data: dict) -> "AgoraResponse":
        """Parse API response into AgoraResponse object.

        Handles both single and multiple service flag responses.

        Args:
            response_data: Raw response from `/api/v2/transpond/webrtc?v=2` endpoint

        Returns:
            Parsed AgoraResponse with extracted addresses and ticket

        """
        # Extract response body
        response_body = response_data.get("response_body", [])
        detail = response_data.get("detail", {})
        if not response_body:
            raise ValueError("No response_body in API response")

        print(response_data)
        # Parse all responses by flag
        responses_by_flag = {}
        first_buffer = None

        for response_item in response_body:
            buffer = response_item.get("buffer", {})
            code = buffer.get("code", -1)

            if code != 0:
                raise Exception(f"Agora API returned error code: {code}")

            flag = buffer.get("flag", 0)
            ticket = buffer.get("cert", "")
            edges_services = buffer.get("edges_services", [])
            detail = {**detail, **buffer.get("detail", {})}
            uid = buffer.get("uid", 0)

            # Derive credentials from UID (matches JavaScript SDK behavior)
            # When ENCRYPT_PROXY_USERNAME_AND_PSW feature flag is enabled:
            # - username = uid as string
            # - password = SHA-256(uid as string)
            # Fallback to detail fields if uid not available
            if uid:
                username = str(uid)
                credentials = derive_password(uid)
            else:
                # Fallback: use detail fields
                username = detail.get("8", "")
                credentials = detail.get("4", "")

            addresses = [
                EdgeAddress(
                    ip=edge["ip"],
                    port=edge["port"],
                    username=username,
                    credentials=credentials,
                    ticket=ticket,
                )
                for edge in edges_services
            ]

            # Store all responses with complete data
            responses_by_flag[flag] = {
                "code": code,
                "addresses": addresses,
                "ticket": ticket,
                "uid": buffer.get("uid", 0),
                "cid": buffer.get("cid", 0),
                "cname": buffer.get("cname", ""),
                "detail": detail,
                "flag": flag,
                "edges_services": edges_services,  # Preserve raw edges_services
            }

            # Use first response for primary fields
            if first_buffer is None:
                first_buffer = buffer

        if first_buffer is None:
            raise ValueError("No valid buffer in response_body")

        # Get the first flag's response data (already parsed with addresses)
        first_flag = first_buffer.get("flag", 0)
        first_response = responses_by_flag.get(first_flag, {})

        # Create response with primary fields from first buffer
        return cls(
            code=first_buffer.get("code", -1),
            addresses=first_response.get(
                "addresses", []
            ),  # Use already-created addresses
            ticket=first_buffer.get("cert", ""),
            uid=first_buffer.get("uid", 0),
            cid=first_buffer.get("cid", 0),
            cname=first_buffer.get("cname", ""),
            server_ts=response_data.get("enter_ts", int(time.time() * 1000)),
            detail=first_buffer.get("detail", {}),
            flag=first_flag,
            opid=response_data.get("opid", 0),
            responses=responses_by_flag if len(responses_by_flag) > 1 else None,
        )

    def get_ice_servers(
        self, turn_port_udp: int = 3478, turn_port_tcp: int = 3433
    ) -> list[ICEServer]:
        """Convert addresses to ICE server configuration.

        Args:
            turn_port_udp: UDP TURN port (default: 3478)
            turn_port_tcp: TCP TURN port (default: 3433)

        Returns:
            List of ICEServer objects ready for RTCPeerConnection

        """
        ice_servers = []

        for addr in self.addresses:
            # UDP TURN server
            ice_servers.append(
                ICEServer(
                    urls=f"turn:{addr.ip.replace('.', '-')}.edge.agora.io:{turn_port_udp}?transport=udp",
                    username=addr.username,
                    credential=addr.credentials,
                )
            )

            # TCP TURN server
            ice_servers.append(
                ICEServer(
                    urls=f"turn:{addr.ip.replace('.', '-')}.edge.agora.io:{turn_port_tcp}?transport=tcp",
                    username=addr.username,
                    credential=addr.credentials,
                )
            )

            # TLS/TURNS server
            ice_servers.append(
                ICEServer(
                    urls=f"turns:{addr.ip.replace('.', '-')}.edge.agora.io:443?transport=tcp",
                    username=addr.username,
                    credential=addr.credentials,
                )
            )
            ice_servers.append(
                ICEServer(
                    urls=f"stun:{addr.ip.replace('.', '-')}.edge.agora.io:{turn_port_tcp}",
                    username=addr.username,
                    credential=addr.credentials,
                )
            )

        return ice_servers

    def get_responses_by_flag(self, flag: int) -> Optional[dict]:
        """Get response data for a specific service flag.

        Args:
            flag: Service flag (e.g., SERVICE_FLAGS["CHOOSE_SERVER"])

        Returns:
            Response data for that flag, or None if not available

        """
        if not self.responses:
            return None
        return self.responses.get(flag)

    def to_ap_response(self, flag: Optional[int] = None) -> dict:
        """Format response data for websocket join_v3 ap_response field.

        Args:
            flag: Specific service flag to format. If None, uses primary response.

        Returns:
            Dictionary formatted for ap_response in join_v3 websocket call

        """
        # Get data for specific flag or use primary response
        if flag is not None and self.responses:
            response_data = self.responses.get(flag)
            if not response_data:
                raise ValueError(f"No response data for flag {flag}")
            return {
                "code": response_data["code"],
                "server_ts": self.server_ts,
                "uid": response_data["uid"],
                "cid": response_data["cid"],
                "cname": response_data["cname"],
                "detail": response_data["detail"],
                "flag": response_data["flag"],
                "opid": self.opid,
                "cert": response_data["ticket"],
                "ticket": response_data["ticket"],
            }
        # Use primary response data
        return {
            "code": self.code,
            "server_ts": self.server_ts,
            "uid": self.uid,
            "cid": self.cid,
            "cname": self.cname,
            "detail": self.detail,
            "flag": self.flag,
            "opid": self.opid,
            "cert": self.ticket,
            "ticket": self.ticket,
        }


class AgoraAPIClient:
    """Client for Agora WebRTC API.

    This client handles creating properly formatted requests to the Agora
    WebRTC server discovery endpoint and parsing responses.
    """

    # List of edge servers from Agora
    WEBCS_DOMAIN = [
        "webrtc2-ap-web-1.agora.io",
        "webrtc2-ap-web-2.agora.io",
        "webrtc2-ap-web-3.agora.io",
        "webrtc2-ap-web-4.agora.io",
    ]

    WEBCS_DOMAIN_BACKUP = [
        "webrtc2-ap-web-5.agora.io",
        "webrtc2-ap-web-6.agora.io",
    ]

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        """Initialize Agora API client.

        Args:
            session: Optional aiohttp ClientSession. If not provided, one will be
                    created for each request.

        """
        self.session = session
        self._own_session = session is None

    async def __aenter__(self):
        """Context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close session if we created it."""
        if self._own_session and self.session:
            await self.session.close()

    async def choose_server(
        self,
        app_id: str,
        token: str,
        channel_name: str,
        user_id: int,
        string_uid: Optional[str] = None,
        role: int = 2,
        area_code: str = "CN,GLOBAL",
        service_flags: Optional[list[int]] = None,
        sid: Optional[str] = None,
        proxy_server: Optional[str] = None,
    ) -> AgoraResponse:
        """Make a request to choose a server (URI 22).

        This is the initial "choose server" request that returns gateway addresses.

        Args:
            app_id: Agora application ID
            token: Authentication token
            channel_name: Channel name to join
            user_id: Numeric user ID
            string_uid: Optional string user ID (defaults to str(user_id))
            role: User role - 1 for host, 2 for audience (default: 2)
            area_code: Preferred area code (default: "CN,GLOBAL")
            service_flags: List of service flags (default: [CHOOSE_SERVER])
            sid: Session ID (generated if not provided)
            proxy_server: Optional HTTP proxy server URL

        Returns:
            AgoraResponse with edge server addresses and ticket

        Raises:
            Exception: If API call fails or returns error code

        """
        if string_uid is None:
            string_uid = str(user_id)

        if service_flags is None:
            service_flags = [11, 26]

        if sid is None:
            sid = str(randint(0, 2**31 - 1))

        # Build request payload
        request_payload = self._build_request_payload(
            app_id=app_id,
            token=token,
            channel_name=channel_name,
            user_id=user_id,
            string_uid=string_uid,
            role=role,
            area_code=area_code,
            service_flags=service_flags,
            sid=sid,
            uri=22,  # Choose server operation
        )
        print(request_payload)
        # Make API call
        response = await self._make_api_call(request_payload, proxy_server=proxy_server)

        # Parse response
        return AgoraResponse.from_api_response(response)

    async def update_ticket(
        self,
        app_id: str,
        token: str,
        channel_name: str,
        user_id: int,
        string_uid: Optional[str] = None,
        edge_addresses: Optional[list[dict]] = None,
        sid: Optional[str] = None,
        service_flags: Optional[list[int]] = None,
        proxy_server: Optional[str] = None,
    ) -> AgoraResponse:
        """Make a request to update ticket (URI 28).

        This is a follow-up request after initial server selection to refresh
        the connection ticket.

        Args:
            app_id: Agora application ID
            token: Authentication token
            channel_name: Channel name
            user_id: Numeric user ID
            string_uid: Optional string user ID
            edge_addresses: List of edge server addresses from previous response
            sid: Session ID
            service_flags: List of service flags
            proxy_server: Optional HTTP proxy server URL

        Returns:
            AgoraResponse with updated ticket

        Raises:
            Exception: If API call fails or returns error code

        """
        if string_uid is None:
            string_uid = str(user_id)

        if service_flags is None:
            service_flags = [SERVICE_FLAGS["CHOOSE_SERVER"]]

        if edge_addresses is None:
            edge_addresses = []

        # Build request payload
        request_payload = self._build_request_payload(
            app_id=app_id,
            token=token,
            channel_name=channel_name,
            user_id=user_id,
            string_uid=string_uid,
            edge_addresses=edge_addresses,
            service_flags=service_flags,
            sid=sid,
            uri=28,  # Ticket update operation
        )

        print(request_payload)

        # Make API call
        response = await self._make_api_call(request_payload, proxy_server=proxy_server)

        # Parse response
        return AgoraResponse.from_api_response(response)

    @staticmethod
    def merge_objects(*objects):
        """Merge multiple dictionaries, filtering out None values.

        Python equivalent of the JavaScript mF function used in Agora SDK.
        Merges objects left to right, skipping None/undefined values.

        Args:
            *objects: Variable number of dictionaries to merge

        Returns:
            Merged dictionary with None values filtered out

        """
        result = {}
        for obj in objects:
            if obj is not None:
                # Merge object, filtering out None values (equivalent to undefined in JS)
                for key, value in obj.items():
                    if value is not None:
                        result[key] = value
        return result

    def _build_request_payload(
        self,
        app_id: str,
        token: str,
        channel_name: str,
        user_id: int,
        string_uid: str,
        service_flags: list[int],
        sid: str,
        uri: int,
        role: int = 2,
        area_code: str = "CN,GLOBAL",
        edge_addresses: Optional[list[dict]] = None,
    ) -> dict:
        """Build the request payload for Agora API.

        Args:
            app_id: Application ID
            token: Auth token
            channel_name: Channel name
            user_id: Numeric user ID
            string_uid: String user ID
            service_flags: Service flags
            sid: Session ID
            uri: Operation URI (22 for choose server, 28 for update ticket)
            role: User role (default: 2 for audience)
            area_code: Area code (default: CN,GLOBAL)
            edge_addresses: Edge addresses for ticket update

        Returns:
            Properly formatted request payload

        """
        client_ts = int(time.time() * 1000)
        opid = randint(0, 10**12 - 1)
        ap_rtm = None
        # Build detail field - matches JavaScript SDK pattern
        # mF(mF(mF({ 6: stringUid, 11: t, 12: USE_NEW_TOKEN ? "1" : undefined },
        #           r ? { 17: r } : {}), {}, { 22: t }, ...)
        # if use new token add "12": "1"
        # "6": string_uid,
        detail = self.merge_objects(
            {"11": area_code},
            {"17": str(role)} if role else {},
            {"22": area_code},
            {"26": "RTM2"} if ap_rtm else {},
        )

        print(detail)
        # Build buffer
        buffer = {
            "cname": channel_name,
            "detail": detail,
            "key": token,
            "service_ids": service_flags,
            "uid": user_id,
        }

        # For ticket update, include edge services
        if edge_addresses:
            buffer["edges_services"] = edge_addresses

        request_payload = {
            "appid": app_id,
            "client_ts": client_ts,
            "opid": opid,
            "sid": sid,
            "request_bodies": [
                {
                    "uri": uri,
                    "buffer": buffer,
                }
            ],
        }

        return request_payload

    async def _make_api_call(
        self, request_payload: dict, proxy_server: Optional[str] = None
    ) -> dict:
        """Make the actual HTTP API call to Agora endpoint.

        Args:
            request_payload: Request payload dictionary
            proxy_server: Optional HTTP proxy URL

        Returns:
            Parsed JSON response from API

        Raises:
            Exception: If request fails or response is invalid

        """
        session = self.session
        should_close = False

        if session is None:
            session = aiohttp.ClientSession()
            should_close = True

        try:
            # Try primary servers
            for domain in self.WEBCS_DOMAIN:
                try:
                    response = await self._call_endpoint(
                        session, domain, request_payload, proxy_server
                    )
                    return response
                except (aiohttp.ClientError, asyncio.TimeoutError, Exception):
                    continue

            # Fall back to backup servers
            for domain in self.WEBCS_DOMAIN_BACKUP:
                try:
                    response = await self._call_endpoint(
                        session, domain, request_payload, proxy_server
                    )
                    return response
                except (aiohttp.ClientError, asyncio.TimeoutError, Exception):
                    continue

            raise Exception("All Agora API servers failed to respond")

        finally:
            if should_close:
                await session.close()

    async def _call_endpoint(
        self,
        session: aiohttp.ClientSession,
        domain: str,
        request_payload: dict,
        proxy_server: Optional[str] = None,
    ) -> dict:
        """Call a single Agora endpoint.

        Args:
            session: aiohttp session
            domain: Server domain
            request_payload: Request payload
            proxy_server: Optional proxy URL

        Returns:
            Parsed JSON response

        """
        url = f"https://{domain}/api/v2/transpond/webrtc?v=2"

        if proxy_server:
            url = f"https://{proxy_server}/ap/?url={domain}/api/v2/transpond/webrtc?v=2"

        # Create FormData with JSON payload
        form_data = aiohttp.FormData()
        form_data.add_field(
            "request", json.dumps(request_payload), content_type="application/json"
        )

        async with session.post(
            url,
            data=form_data,
            timeout=aiohttp.ClientTimeout(total=10),
            ssl=False,  # Note: In production, verify SSL certificates
        ) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}: {await resp.text()}")

            response_data = await resp.json()
            return response_data


async def main():
    """Example usage of the Agora API client."""
    # Example parameters
    app_id = "5bcfed01952d4f2eb306c98b8908da93"
    token = "007eJxTYGCdnp61senSMbsiwUs+Uo+XKRzP94xl0Toga77vQdWjjHoFBtOk5LTUFANDS1OjFJM0o9QkYwOzZEuLJAtLA4uUREvj+1wimQJ8DAyylldOMzJAIIgvxRAaUpBU7u5sXlaRYuJS4FeW5ObuYwAGHAxGhkbGhgamFgD+7CVz"
    channel_name = "UTpbwGC7vxd4DpNvbFGL000000"
    user_id = 21231058
    string_uid = "client_21231"

    # Make request
    async with AgoraAPIClient() as client:
        try:
            print("=== Example 1: Get media gateway only ===")
            # Choose server - media gateway only
            response = await client.choose_server(
                app_id=app_id,
                token=token,
                channel_name=channel_name,
                user_id=user_id,
                string_uid=string_uid,
                role=2,  # 2 = audience
            )

            print(f"Successfully got {len(response.addresses)} edge addresses:")
            for addr in response.addresses:
                print(f"  - {addr.ip.replace('.', '-')}.edge.agora.io:{addr.port}")
            print(f"Ticket: {response.ticket[:50]}...")

            print("\n=== Example 2: Get both media gateway AND TURN servers ===")
            # Request with both service flags to get TURN servers
            response = await client.choose_server(
                app_id=app_id,
                token=token,
                channel_name=channel_name,
                user_id=user_id,
                string_uid=string_uid,
                role=2,
                service_flags=[
                    SERVICE_FLAGS["CHOOSE_SERVER"],  # Media gateway
                    SERVICE_FLAGS["CLOUD_PROXY"],  # TURN servers
                ],
            )

            # Get separate responses by flag
            gateway_resp = response.get_responses_by_flag(
                SERVICE_FLAGS["CHOOSE_SERVER"]
            )
            turn_resp = response.get_responses_by_flag(SERVICE_FLAGS["CLOUD_PROXY"])

            if gateway_resp:
                print(f"\nGateway addresses: {len(gateway_resp['addresses'])}")
                for addr in gateway_resp["addresses"]:
                    print(f"  - {addr.ip.replace('.', '-')}.edge.agora.io:{addr.port}")

            if turn_resp:
                print(f"\nTURN addresses: {len(turn_resp['addresses'])}")
                for addr in turn_resp["addresses"]:
                    print(f"  - {addr.ip.replace('.', '-')}.edge.agora.io:{addr.port}")

            # Get ICE servers (TURN) configuration
            ice_servers = response.get_ice_servers()
            print(f"\nICE Servers configuration ({len(ice_servers)} entries):")
            for server in ice_servers[:3]:  # Show first 3
                print(f"  - {server.urls}")
                print(f"    username: {server.username}")
                print(f"    credential: {server.credential}")

            # Format for WebRTC RTCPeerConnection
            rtc_config = {"iceServers": [server.to_dict() for server in ice_servers]}
            print("\nRTC Configuration ready for RTCPeerConnection")
            print(f"Total ICE servers: {len(rtc_config['iceServers'])}")

        except Exception as e:
            print(f"Error: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
