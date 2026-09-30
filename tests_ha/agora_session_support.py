"""A hand-written stand-in for ``pyagorartc.AgoraSession`` plus real token/AP values.

The fake keeps the contract the camera relies on: ``join`` answers or raises after
``on_closed(JOIN_FAILED)``, ``close`` is idempotent and fires
``on_closed(CLOSED_BY_HOST)`` once, and ``end`` plays a session ending on its own.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pyagorartc import APResponse, CloseReason, IceCandidate
from pymammotion.http.model.camera_stream import Camera, StreamSubscriptionResponse

from custom_components.mammotion import camera as camera_module

ANSWER_SDP = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\n"

# One gateway block (flag 4096) and one TURN block, as choose_server returns them.
AP_PAYLOAD: dict[str, Any] = {
    "enter_ts": 1790716795180,
    "opid": 1,
    "detail": {},
    "response_body": [
        {
            "uri": 23,
            "buffer": {
                "code": 0,
                "flag": 4096,
                "uid": 12345678,
                "cid": 1,
                "cname": "channel-1",
                "cert": "ticket",
                "detail": {},
                "edges_services": [{"ip": "203.0.113.10", "port": 4713}],
            },
        },
        {
            "uri": 23,
            "buffer": {
                "code": 0,
                "flag": 4194310,
                "uid": 12345678,
                "cid": 2,
                "cname": "channel-1",
                "cert": "turn-ticket",
                "detail": {},
                "edges_services": [
                    {"ip": "198.51.100.20", "port": 443},
                    {"ip": "198.51.100.21", "port": 443},
                ],
            },
        },
    ],
}


def make_ap_response() -> APResponse:
    """Parse the canned access-point answer with the library's own parser."""
    return APResponse.from_api_response(AP_PAYLOAD)


def make_stream_data(**overrides: Any) -> StreamSubscriptionResponse:
    """Build the cloud's stream token, shaped as the API returns it."""
    fields: dict[str, Any] = {
        "appid": "app-id",
        "openEncrypt": 0,
        "cameras": [Camera(cameraId=0, token="camera-token")],
        "channelName": "channel-1",
        "areaCode": "AREA_CODE_EU",
        "token": "channel-token",
        "uid": 12345678,
        "license": "license-1",
        "availableTime": 600,
    }
    fields.update(overrides)
    return StreamSubscriptionResponse(**fields)


class FakeAgoraSession:
    """Records what the camera hands a session and plays its lifecycle."""

    def __init__(
        self,
        creds: Any = None,
        ap: Any = None,
        *,
        on_closed: Callable[[CloseReason], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> None:
        """Keep every argument so a test can read back how it was built."""
        self.creds = creds
        self.ap = ap
        self.on_closed = on_closed
        self.kwargs = kwargs
        self.candidates: list[IceCandidate] = []
        self.joined_with: tuple[str, str] | None = None
        self.close_calls = 0
        self.close_reason: CloseReason | None = None
        self.answer = ANSWER_SDP
        self.join_error: Exception | None = None
        # May be a coroutine function: joins are awaited, so the host can act mid-join.
        self.on_join: Callable[[], Awaitable[None] | None] | None = None
        self.on_close: Callable[[], None] | None = None

    def add_ice_candidate(self, candidate: IceCandidate) -> None:
        """Queue a candidate, as the real session does before join."""
        self.candidates.append(candidate)

    async def join(self, offer_sdp: str, session_id: str) -> str:
        """Answer, or end with JOIN_FAILED and raise, as the real session does."""
        self.joined_with = (offer_sdp, session_id)
        if self.on_join is not None and (pending := self.on_join()) is not None:
            await pending
        if self.join_error is not None:
            await self.end(CloseReason.JOIN_FAILED)
            raise self.join_error
        return self.answer

    async def close(self) -> None:
        """End the session on the host's behalf; later calls are no-ops."""
        self.close_calls += 1
        if self.on_close is not None:
            self.on_close()
        await self.end(CloseReason.CLOSED_BY_HOST)

    async def end(self, reason: CloseReason) -> None:
        """End the session once, telling the host why."""
        if self.close_reason is not None:
            return
        self.close_reason = reason
        if self.on_closed is not None:
            await self.on_closed(reason)


def install_fake_sessions(
    monkeypatch: pytest.MonkeyPatch, **attrs: Any
) -> list[FakeAgoraSession]:
    """Make the camera build fakes given ``attrs``; returns them in creation order."""
    created: list[FakeAgoraSession] = []

    def factory(*args: Any, **kwargs: Any) -> FakeAgoraSession:
        session = FakeAgoraSession(*args, **kwargs)
        for name, value in attrs.items():
            setattr(session, name, value)
        created.append(session)
        return session

    monkeypatch.setattr(camera_module, "AgoraSession", factory)
    return created
