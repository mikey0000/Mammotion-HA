"""How the camera drives one pyagorartc session per WebRTC offer.

The session itself is the library's and is tested there; these pin the host glue:
which uid and deadline a session is built with, what the keep-alive and peer
recovery callbacks do on the mower, how join failures and session endings reach
the viewer, and which browser candidates make it into the join.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from agora_session_support import (
    ANSWER_SDP,
    FakeAgoraSession,
    install_fake_sessions,
    make_ap_response,
    make_stream_data,
)
from homeassistant.components.camera import WebRTCAnswer, WebRTCError
from homeassistant.core import HomeAssistant
from pyagorartc import (
    CloseReason,
    GatewayConnectError,
    IceCandidate,
    JoinRejectedError,
    JoinTimeoutError,
    SdpError,
    SessionOptions,
)
from pymammotion.http.model.camera_stream import StreamSubscriptionResponse
from pymammotion.http.model.http import Response
from webrtc_models import RTCIceCandidateInit

from custom_components.mammotion.camera import (
    CAMERAS,
    MammotionWebRTCCamera,
    async_setup_platform_services,
)
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator

_DEVICE = "Yuka-000CLD"
_CANDIDATE = "candidate:1 1 udp 2122260223 192.168.1.20 54321 typ host"
_LATE_CANDIDATE = "candidate:7 1 udp 1686052607 203.0.113.7 40000 typ srflx"


def _coordinator(stream: StreamSubscriptionResponse | None = None) -> MagicMock:
    coordinator = MagicMock(spec=MammotionBaseUpdateCoordinator)
    coordinator.device = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.unique_name = _DEVICE
    coordinator.device.device_name = _DEVICE
    coordinator.device.iot_id = "iot-123"
    coordinator.is_on_4g = False
    coordinator.all_cameras_streaming = True
    coordinator.streams_all_cameras = True
    coordinator.async_check_stream_expiry = AsyncMock(
        return_value=(stream or make_stream_data(), make_ap_response())
    )
    coordinator.async_send_command = AsyncMock()
    coordinator.async_register_camera_session = AsyncMock()
    coordinator.async_release_camera_session = AsyncMock()
    coordinator.manager.get_stream_subscription = AsyncMock()
    return coordinator


def _camera(
    hass: HomeAssistant, coordinator: MagicMock, slot: int = 0
) -> MammotionWebRTCCamera:
    camera = MammotionWebRTCCamera(coordinator, CAMERAS[slot], hass)
    camera.hass = hass
    camera.entity_id = f"camera.{CAMERAS[slot].key}"
    return camera


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[FakeAgoraSession]:
    """Every session the camera builds, in order."""
    return install_fake_sessions(monkeypatch)


@pytest.fixture
def coordinator() -> MagicMock:
    """Build a coordinator on WiFi holding a fresh token and AP answer."""
    return _coordinator()


@pytest.fixture
def camera(hass: HomeAssistant, coordinator: MagicMock) -> MammotionWebRTCCamera:
    """Build the left camera of a Yuka."""
    return _camera(hass, coordinator)


async def _offer(
    camera: MammotionWebRTCCamera, session_id: str = "session-1"
) -> MagicMock:
    send_message = MagicMock()
    await camera.async_handle_async_webrtc_offer("offer-sdp", session_id, send_message)
    return send_message


@pytest.mark.parametrize("slot", [0, 1, 2])
async def test_each_feed_joins_for_its_own_publisher_uid(
    hass: HomeAssistant,
    coordinator: MagicMock,
    sessions: list[FakeAgoraSession],
    slot: int,
) -> None:
    """Slot N of cameraStates is published as Agora uid N + 1."""
    await _offer(_camera(hass, coordinator, slot))

    assert sessions[0].kwargs["options"] == SessionOptions(
        client_codec="vp8", target_uid=slot + 1
    )


async def test_the_session_is_built_from_the_token_and_the_ap_answer(
    camera: MammotionWebRTCCamera, sessions: list[FakeAgoraSession]
) -> None:
    """The viewer uid and channel come from the stream token, the edges from the AP."""
    send_message = await _offer(camera)

    session = sessions[0]
    assert (session.creds.channel_name, session.creds.uid, session.creds.token) == (
        "channel-1",
        12345678,
        "channel-token",
    )
    assert session.ap.get_gateway_addresses()[0].ip == "203.0.113.10"
    assert session.kwargs["clock"] is time.monotonic
    assert session.joined_with == ("offer-sdp", "session-1")
    send_message.assert_called_once_with(WebRTCAnswer(ANSWER_SDP))


async def test_a_4g_session_is_built_with_the_budget_deadline(
    camera: MammotionWebRTCCamera,
    coordinator: MagicMock,
    sessions: list[FakeAgoraSession],
) -> None:
    """``availableTime`` is the cloud's cellular budget, counted from the join."""
    coordinator.is_on_4g = True

    before = time.monotonic()
    await _offer(camera)
    after = time.monotonic()

    assert before + 600 <= sessions[0].kwargs["deadline"] <= after + 600


@pytest.mark.parametrize(
    ("on_4g", "available_time"),
    [(False, 600), (True, None), (True, 0)],
    ids=["wifi", "4g-no-budget", "4g-zero-budget"],
)
async def test_no_deadline_without_a_4g_budget(
    hass: HomeAssistant,
    sessions: list[FakeAgoraSession],
    on_4g: bool,
    available_time: int | None,
) -> None:
    """WiFi streams stay unbounded, as the old keep-alive loop left them."""
    coordinator = _coordinator(make_stream_data(availableTime=available_time))
    coordinator.is_on_4g = on_4g

    await _offer(_camera(hass, coordinator))

    assert sessions[0].kwargs["deadline"] is None


async def test_keepalive_stops_on_wifi_without_poking_the_mower(
    camera: MammotionWebRTCCamera, coordinator: MagicMock
) -> None:
    """False tells the session to stop calling; WiFi needs no ``refresh_fpv``."""
    assert await camera._fpv_keepalive() is False
    coordinator.async_send_command.assert_not_awaited()


async def test_keepalive_rearms_the_encoder_on_4g(
    camera: MammotionWebRTCCamera, coordinator: MagicMock
) -> None:
    """Over cellular the encoder stops publishing unless it is poked."""
    coordinator.is_on_4g = True

    assert await camera._fpv_keepalive() is True
    coordinator.async_send_command.assert_awaited_once_with("refresh_fpv")


async def test_session_callbacks_are_the_cameras_own(
    camera: MammotionWebRTCCamera, sessions: list[FakeAgoraSession]
) -> None:
    """The library calls back into the host glue, not a handler of its own."""
    await _offer(camera)

    kwargs = sessions[0].kwargs
    assert kwargs["keepalive"] == camera._fpv_keepalive
    assert kwargs["on_peer_left"] == camera._on_peer_left
    assert kwargs["spawn"] == camera._spawn_session_task
    assert sessions[0].on_closed == camera._on_closed


async def test_a_departed_publisher_is_nudged_back_then_resubscribed(
    camera: MammotionWebRTCCamera, coordinator: MagicMock
) -> None:
    """BLE sync first, then a fresh subscription so the mower rejoins the channel."""
    order = MagicMock()
    coordinator.async_send_command.side_effect = order.sync
    coordinator.manager.get_stream_subscription.side_effect = order.subscribe

    await camera._on_peer_left(1)

    assert order.mock_calls == [
        call.sync("send_todev_ble_sync", sync_type=3),
        call.subscribe(_DEVICE, "iot-123", all_cameras=True),
    ]


async def test_session_tasks_run_as_home_assistant_background_tasks(
    camera: MammotionWebRTCCamera,
) -> None:
    """HA tracks and cancels them on shutdown; the name says whose they are."""
    ran = asyncio.Event()

    async def work() -> None:
        ran.set()

    task = camera._spawn_session_task(work())
    await asyncio.wait_for(task, 1)

    assert ran.is_set()
    assert task.get_name() == "mammotion agora camera.webrtc_camera"


@pytest.mark.parametrize(
    "error",
    [
        GatewayConnectError("no edge accepted the socket"),
        JoinRejectedError(2003, "repeat join"),
        JoinTimeoutError("no join result"),
        SdpError("offer has no video"),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_a_failed_join_is_one_error_and_releases_the_feed(
    hass: HomeAssistant,
    camera: MammotionWebRTCCamera,
    coordinator: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """No fabricated answer: the viewer gets a 500, and JOIN_FAILED adds no 503."""
    install_fake_sessions(monkeypatch, join_error=error)

    send_message = await _offer(camera)
    await hass.async_block_till_done()

    [sent] = [c.args[0] for c in send_message.call_args_list]
    assert isinstance(sent, WebRTCError)
    assert sent.code == "500"
    assert camera._sessions == {}
    assert camera._session is None
    coordinator.async_release_camera_session.assert_awaited_once_with("webrtc_camera")


@pytest.mark.regression
async def test_a_token_the_host_cannot_convert_is_one_error_and_releases_the_feed(
    hass: HomeAssistant,
    camera: MammotionWebRTCCamera,
    coordinator: MagicMock,
    sessions: list[FakeAgoraSession],
) -> None:
    """A salt that is not base64 raised out of the offer handler, so no 500 reached the viewer."""
    coordinator.async_check_stream_expiry.return_value = (
        make_stream_data(openEncrypt=1, key="secret", salt="abcde"),
        make_ap_response(),
    )

    send_message = await _offer(camera)
    await hass.async_block_till_done()

    [sent] = [c.args[0] for c in send_message.call_args_list]
    assert isinstance(sent, WebRTCError)
    assert sent.code == "500"
    assert sessions == []
    assert camera._session is None
    coordinator.async_release_camera_session.assert_awaited_once_with("webrtc_camera")


async def test_an_offer_without_an_agora_edge_fails_before_any_session(
    hass: HomeAssistant,
    camera: MammotionWebRTCCamera,
    coordinator: MagicMock,
    sessions: list[FakeAgoraSession],
) -> None:
    """A token without an AP answer has nowhere to connect."""
    coordinator.async_check_stream_expiry.return_value = (make_stream_data(), None)

    send_message = await _offer(camera)
    await hass.async_block_till_done()

    assert sessions == []
    assert send_message.call_args.args[0].code == "500"
    coordinator.async_release_camera_session.assert_awaited_once_with("webrtc_camera")


async def test_a_new_offer_closes_the_previous_session_first(
    camera: MammotionWebRTCCamera, sessions: list[FakeAgoraSession]
) -> None:
    """A session is single use; the feed holds at most one."""
    await _offer(camera, "session-1")
    await _offer(camera, "session-2")

    assert [s.close_calls for s in sessions] == [1, 0]
    assert camera._session is sessions[1]


async def test_candidates_sent_during_negotiation_reach_the_join(
    camera: MammotionWebRTCCamera,
    coordinator: MagicMock,
    sessions: list[FakeAgoraSession],
) -> None:
    """The browser trickles while the token is fetched; those go in the join."""
    fresh = coordinator.async_check_stream_expiry.return_value

    async def token_with_trickle(**_kwargs: object) -> object:
        await camera.async_on_webrtc_candidate(
            "session-1",
            RTCIceCandidateInit(_CANDIDATE, sdp_mid="0", sdp_m_line_index=0),
        )
        await camera.async_on_webrtc_candidate(
            "stale-session",
            RTCIceCandidateInit("candidate:9 1 udp 1 10.0.0.9 9 typ host"),
        )
        return fresh

    coordinator.async_check_stream_expiry.side_effect = token_with_trickle

    await _offer(camera)
    await camera.async_on_webrtc_candidate("session-1", RTCIceCandidateInit(_CANDIDATE))

    assert sessions[0].candidates == [
        IceCandidate(_CANDIDATE, sdp_mid="0", sdp_mline_index=0)
    ]


@pytest.mark.regression
async def test_a_candidate_sent_after_the_join_started_is_dropped(
    camera: MammotionWebRTCCamera,
    coordinator: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A mid-join candidate was queued for the next offer; only the per-offer reset kept it out."""
    fresh = coordinator.async_check_stream_expiry.return_value
    early = {
        "session-1": _CANDIDATE,
        "session-2": "candidate:2 1 udp 1 10.0.0.2 9 typ host",
    }

    async def token_with_trickle(**_kwargs: object) -> object:
        session_id = camera._pending_offer_id
        await camera.async_on_webrtc_candidate(
            session_id, RTCIceCandidateInit(early[session_id])
        )
        return fresh

    async def trickle_mid_join() -> None:
        await camera.async_on_webrtc_candidate(
            camera._pending_offer_id, RTCIceCandidateInit(_LATE_CANDIDATE)
        )

    coordinator.async_check_stream_expiry.side_effect = token_with_trickle
    sessions = install_fake_sessions(monkeypatch, on_join=trickle_mid_join)

    with caplog.at_level(logging.DEBUG, logger="custom_components.mammotion.camera"):
        await _offer(camera, "session-1")
        await _offer(camera, "session-2")

    assert [s.candidates for s in sessions] == [
        [IceCandidate(early["session-1"], sdp_mline_index=0)],
        [IceCandidate(early["session-2"], sdp_mline_index=0)],
    ]
    assert caplog.text.count("its join has started") == 2


@pytest.mark.regression
async def test_a_replaced_session_ending_itself_does_not_end_the_next_viewer(
    camera: MammotionWebRTCCamera,
    coordinator: MagicMock,
    sessions: list[FakeAgoraSession],
) -> None:
    """The old session's GATEWAY_QUIT, landing while it was closed for a new offer, 503'd the new viewer."""
    await _offer(camera, "session-1")
    replaced = sessions[0]

    async def quit_while_closing() -> None:
        replaced.close_calls += 1
        await replaced.end(CloseReason.GATEWAY_QUIT)

    replaced.close = quit_while_closing  # type: ignore[method-assign]

    send_message = await _offer(camera, "session-2")

    send_message.assert_called_once_with(WebRTCAnswer(ANSWER_SDP))
    assert "session-2" in camera._sessions
    assert camera._session is sessions[1]
    assert sessions[1].close_calls == 0
    coordinator.async_release_camera_session.assert_not_awaited()


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        (CloseReason.GATEWAY_QUIT, "Another camera on this mower took over the stream"),
        (CloseReason.DEADLINE, "4G streaming budget exhausted"),
        (CloseReason.SOCKET_CLOSED, "Stream lost"),
        (CloseReason.P2P_LOST, "Stream lost"),
    ],
)
async def test_a_session_that_ends_itself_tells_the_viewer_why(
    camera: MammotionWebRTCCamera,
    coordinator: MagicMock,
    sessions: list[FakeAgoraSession],
    reason: CloseReason,
    message: str,
) -> None:
    """The card shows a reason instead of freezing, and the feed is released."""
    send_message = await _offer(camera)
    send_message.reset_mock()

    await sessions[0].end(reason)

    error = send_message.call_args.args[0]
    assert (error.code, error.message) == ("503", message)
    assert camera._sessions == {}
    assert camera._session is None
    coordinator.async_release_camera_session.assert_awaited_once_with("webrtc_camera")


async def test_an_encrypted_channel_is_flagged_in_the_log(
    camera: MammotionWebRTCCamera,
    coordinator: MagicMock,
    sessions: list[FakeAgoraSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The library cannot decrypt, so openEncrypt means no picture (Q6)."""
    coordinator.async_check_stream_expiry.return_value = (
        make_stream_data(openEncrypt=1, key="secret", salt="c2FsdA=="),
        make_ap_response(),
    )

    with caplog.at_level(logging.WARNING, logger="custom_components.mammotion.camera"):
        await _offer(camera)

    assert "openEncrypt=1" in caplog.text
    assert sessions[0].creds.encryption is not None


@pytest.mark.regression
async def test_the_refresh_stream_service_does_not_log_the_token(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The debug line printed the whole subscription response, channel token included."""
    reporting = MagicMock(spec=MammotionBaseUpdateCoordinator)
    mower = SimpleNamespace(
        device=SimpleNamespace(device_name=_DEVICE, iot_id="iot-123"),
        api=SimpleNamespace(
            get_stream_subscription=AsyncMock(
                return_value=Response(code=0, msg="ok", data=make_stream_data())
            )
        ),
        reporting_coordinator=reporting,
    )
    entry = SimpleNamespace(runtime_data=SimpleNamespace(mowers=[mower]))
    hass.states.async_set("camera.webrtc_camera", "idle", {"model_name": _DEVICE})
    await async_setup_platform_services(hass, entry)

    with caplog.at_level(logging.DEBUG, logger="custom_components.mammotion.camera"):
        await hass.services.async_call(
            "mammotion",
            "refresh_stream",
            {"entity_id": "camera.webrtc_camera"},
            blocking=True,
        )

    reporting.set_stream_data.assert_called_once()
    assert "Refresh stream data" in caplog.text
    assert "channel-token" not in caplog.text
