"""The WHEP endpoint go2rtc pulls a camera feed from.

Requests go through Home Assistant's real HTTP server to the real camera
entity, whose Agora session is the hand-written fake.  These pin the HTTP
contract go2rtc relies on, the token check, and that the mower's stream is
released when the last WHEP session for a feed ends.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from unittest.mock import MagicMock

import pytest
from agora_session_support import (
    ANSWER_SDP,
    FakeAgoraSession,
    install_fake_sessions,
    make_camera_coordinator,
)
from aiohttp.test_utils import TestClient
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pyagorartc import JoinTimeoutError
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from restream_support import (
    GO2RTC_STREAMS,
    HA_INTERNAL_URL,
    OFFER_SDP,
    STREAM_LEFT,
    make_restreamer,
    make_yuka_cameras,
)

from custom_components.mammotion.camera import MammotionWebRTCCamera
from custom_components.mammotion.restream import WHEP_RETRY_BACKOFF, Go2RtcRestreamer

type ClientFactory = Callable[[], Awaitable[TestClient]]

_WHEP = f"/api/mammotion/whep/{STREAM_LEFT}"
_SDP = {"Content-Type": "application/sdp"}
_TIMEOUT = 5


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[FakeAgoraSession]:
    """Every Agora session the cameras build, in order."""
    return install_fake_sessions(monkeypatch)


@pytest.fixture
def coordinator() -> MagicMock:
    """Build the Yuka's coordinator, observed for stream release."""
    return make_camera_coordinator()


@pytest.fixture
def cameras(hass: HomeAssistant, coordinator: MagicMock) -> list[MammotionWebRTCCamera]:
    """Build the Yuka's three cameras."""
    return make_yuka_cameras(hass, coordinator)


@pytest.fixture
async def restreamer(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    cameras: list[MammotionWebRTCCamera],
) -> Go2RtcRestreamer:
    """Start a restreamer against a go2rtc that answers every call."""
    assert await async_setup_component(hass, "http", {})
    await hass.config.async_update(internal_url=HA_INTERNAL_URL)
    aioclient_mock.get(GO2RTC_STREAMS, json={})
    aioclient_mock.patch(GO2RTC_STREAMS)
    restreamer = make_restreamer(hass, cameras)
    await restreamer.async_start()
    await hass.async_block_till_done(wait_background_tasks=True)
    return restreamer


@pytest.fixture
async def client(
    restreamer: Go2RtcRestreamer, hass_client_no_auth: ClientFactory
) -> TestClient:
    """Return an HTTP client with no Home Assistant credentials, as go2rtc is."""
    return await hass_client_no_auth()


def _token(restreamer: Go2RtcRestreamer, name: str = STREAM_LEFT) -> dict[str, str]:
    return {"token": restreamer.feeds[name].token}


async def test_an_offer_is_answered_with_the_agora_sdp(
    client: TestClient,
    restreamer: Go2RtcRestreamer,
    sessions: list[FakeAgoraSession],
) -> None:
    """go2rtc reads the body as the answer whatever the status, so the body is the SDP."""
    response = await client.post(
        _WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP
    )

    assert response.status == HTTPStatus.CREATED
    assert response.content_type == "application/sdp"
    assert await response.text() == ANSWER_SDP
    session_id = restreamer.feeds[STREAM_LEFT].session_id
    assert response.headers["Location"] == f"{_WHEP}/{session_id}"
    assert sessions[0].joined_with == (OFFER_SDP, session_id)


@pytest.mark.parametrize(
    ("path", "params", "status"),
    [
        pytest.param(_WHEP, {"token": "wrong"}, HTTPStatus.UNAUTHORIZED, id="token"),
        pytest.param(_WHEP, {}, HTTPStatus.UNAUTHORIZED, id="no-token"),
        pytest.param(
            "/api/mammotion/whep/mammotion_other_camera",
            {"token": "x"},
            HTTPStatus.NOT_FOUND,
            id="unknown-stream",
        ),
    ],
)
async def test_offers_without_the_feed_token_never_reach_agora(
    client: TestClient,
    sessions: list[FakeAgoraSession],
    path: str,
    params: dict[str, str],
    status: HTTPStatus,
) -> None:
    """The view is open to unauthenticated callers, so the token is the only gate."""
    response = await client.post(path, params=params, data=OFFER_SDP, headers=_SDP)

    assert response.status == status
    assert sessions == []


async def test_a_feed_token_does_not_open_another_feed(
    client: TestClient, restreamer: Go2RtcRestreamer, sessions: list[FakeAgoraSession]
) -> None:
    """Each feed checks its own token."""
    right = next(name for name in restreamer.feeds if name.endswith("_right"))

    response = await client.post(
        _WHEP, params=_token(restreamer, right), data=OFFER_SDP, headers=_SDP
    )

    assert response.status == HTTPStatus.UNAUTHORIZED
    assert sessions == []


async def test_an_empty_offer_is_a_bad_request(
    client: TestClient, restreamer: Go2RtcRestreamer, sessions: list[FakeAgoraSession]
) -> None:
    """Nothing is started for an offer with no SDP in it."""
    response = await client.post(_WHEP, params=_token(restreamer), data="  ")

    assert response.status == HTTPStatus.BAD_REQUEST
    assert sessions == []


async def test_a_failed_join_reports_the_camera_error_and_releases_the_stream(
    client: TestClient,
    restreamer: Go2RtcRestreamer,
    coordinator: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mower must not keep publishing for a WHEP session that never started."""
    install_fake_sessions(monkeypatch, join_error=JoinTimeoutError("no join reply"))

    response = await client.post(
        _WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP
    )

    assert response.status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "no join reply" in await response.text()
    assert restreamer.feeds[STREAM_LEFT].session_id is None
    coordinator.async_release_camera_session.assert_awaited_once_with("webrtc_camera")


async def test_delete_ends_the_session_and_releases_the_stream(
    hass: HomeAssistant,
    client: TestClient,
    restreamer: Go2RtcRestreamer,
    coordinator: MagicMock,
    sessions: list[FakeAgoraSession],
) -> None:
    """A WHEP client that says goodbye stops the mower publishing."""
    answered = await client.post(
        _WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP
    )

    response = await client.delete(
        answered.headers["Location"], params=_token(restreamer)
    )
    await hass.async_block_till_done()

    assert response.status == HTTPStatus.OK
    assert sessions[0].close_calls == 1
    coordinator.async_release_camera_session.assert_awaited_once_with("webrtc_camera")


async def test_delete_of_a_session_that_is_no_longer_current_is_not_found(
    client: TestClient, restreamer: Go2RtcRestreamer, sessions: list[FakeAgoraSession]
) -> None:
    """A late goodbye for a replaced session must not end its successor."""
    first = await client.post(
        _WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP
    )
    await client.post(_WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP)

    response = await client.delete(first.headers["Location"], params=_token(restreamer))

    assert response.status == HTTPStatus.NOT_FOUND
    assert sessions[1].close_calls == 0


@pytest.mark.regression
@pytest.mark.usefixtures("sessions")
async def test_a_redial_without_goodbye_does_not_keep_the_stream_alive(
    hass: HomeAssistant,
    client: TestClient,
    restreamer: Go2RtcRestreamer,
    coordinator: MagicMock,
) -> None:
    """go2rtc redials with a fresh offer and never DELETEs the old session.

    Left in the camera's viewer set, the old session kept a viewer counted after
    the new one ended, so the mower was never told to stop publishing.
    """
    await client.post(_WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP)
    second = await client.post(
        _WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP
    )

    await client.delete(second.headers["Location"], params=_token(restreamer))
    await hass.async_block_till_done()

    coordinator.async_release_camera_session.assert_awaited_once_with("webrtc_camera")


async def test_after_a_failed_join_offers_wait_out_a_backoff(
    client: TestClient,
    restreamer: Go2RtcRestreamer,
    monkeypatch: pytest.MonkeyPatch,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Frigate retries every few seconds; each try costs a cloud token and a mower wake."""
    failing = install_fake_sessions(monkeypatch, join_error=JoinTimeoutError("no"))
    await client.post(_WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP)

    refused = await client.post(
        _WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP
    )
    freezer.tick(WHEP_RETRY_BACKOFF[0])
    sessions = install_fake_sessions(monkeypatch)
    retried = await client.post(
        _WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP
    )

    assert refused.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert refused.headers["Retry-After"] == str(WHEP_RETRY_BACKOFF[0] + 1)
    assert len(failing) == 1, "the refused offer must not reach Agora"
    assert retried.status == HTTPStatus.CREATED
    assert len(sessions) == 1


async def test_an_offer_refused_as_busy_does_not_start_a_backoff(
    hass: HomeAssistant,
    client: TestClient,
    restreamer: Go2RtcRestreamer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 409 means another join is still running; the feed itself is fine."""
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    install_fake_sessions(monkeypatch, on_join=hold)
    first = asyncio.ensure_future(
        client.post(_WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP)
    )
    await _wait_for(restreamer.feeds[STREAM_LEFT].camera._join_lock.locked)

    busy = await client.post(
        _WHEP, params=_token(restreamer), data=OFFER_SDP, headers=_SDP
    )
    release.set()
    answered = await asyncio.wait_for(first, _TIMEOUT)

    assert busy.status == HTTPStatus.CONFLICT
    assert answered.status == HTTPStatus.CREATED
    assert restreamer.feeds[STREAM_LEFT].retry_at == 0.0


async def _wait_for(condition: Callable[[], bool]) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), _TIMEOUT)
