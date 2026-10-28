"""Keeping the camera feeds registered on an external go2rtc server.

go2rtc (Frigate's, typically) is driven through a mocked HTTP API; the cameras
are the real entities over the fake Agora session.  These pin what reaches
go2rtc and when, and when a feed go2rtc stopped pulling is closed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from agora_session_support import (
    FakeAgoraSession,
    install_fake_sessions,
    make_camera_coordinator,
)
from aiohttp import ClientError, encode_basic_auth
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from restream_support import (
    GO2RTC_STREAMS,
    HA_INTERNAL_URL,
    OFFER_SDP,
    STREAM_LEFT,
    STREAM_REAR,
    STREAM_RIGHT,
    idle_stream,
    make_restreamer,
    make_yuka_cameras,
    pulled_stream,
    whep_source,
)
from yarl import URL

from custom_components.mammotion.restream import (
    DATA_WHEP_FEEDS,
    GO2RTC_SYNC_INTERVAL,
    Go2RtcRestreamer,
    go2rtc_stream_name,
    validate_go2rtc_url,
)

_ALL_STREAMS = (STREAM_LEFT, STREAM_RIGHT, STREAM_REAR)


@pytest.fixture(autouse=True)
async def http(hass: HomeAssistant) -> None:
    """Serve Home Assistant's HTTP views at a known internal URL."""
    assert await async_setup_component(hass, "http", {})
    await hass.config.async_update(internal_url=HA_INTERNAL_URL)


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[FakeAgoraSession]:
    """Every Agora session the cameras build, in order."""
    return install_fake_sessions(monkeypatch)


def _patches(aioclient_mock: AiohttpClientMocker) -> dict[str, str]:
    """Return the source each PATCH to go2rtc set, by stream name."""
    return {
        url.query["name"]: url.query["src"]
        for method, url, *_ in aioclient_mock.mock_calls
        if method == "patch"
    }


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return what the restreamer logged at WARNING or above."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "custom_components.mammotion.restream"
        and r.levelno >= logging.WARNING
    ]


async def _start(hass: HomeAssistant, restreamer: Go2RtcRestreamer) -> None:
    await restreamer.async_start()
    await hass.async_block_till_done(wait_background_tasks=True)


async def _sync(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(GO2RTC_SYNC_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _mock_streams(aioclient_mock: AiohttpClientMocker, streams: dict[str, Any]) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(GO2RTC_STREAMS, json=streams)
    aioclient_mock.patch(GO2RTC_STREAMS)
    aioclient_mock.delete(GO2RTC_STREAMS)


@pytest.mark.parametrize(
    ("device_name", "key", "expected"),
    [
        pytest.param("Yuka-000CLD", "webrtc_camera", STREAM_LEFT, id="left"),
        pytest.param(
            "Luba-MNAB12CD",
            "webrtc_camera_right",
            "mammotion_luba_mnab12cd_camera_right",
            id="right",
        ),
        pytest.param(
            "Luba-VAME9R5S",
            "webrtc_camera_vision",
            "mammotion_luba_vame9r5s_camera_vision",
            id="vision",
        ),
    ],
)
def test_stream_names_are_the_device_and_camera_slugged(
    device_name: str, key: str, expected: str
) -> None:
    """The name a user types into Frigate must not change with entity renames."""
    assert go2rtc_stream_name(device_name, key) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(" http://frigate:1984 ", "http://frigate:1984", id="trimmed"),
        pytest.param("https://nvr.lan/go2rtc", "https://nvr.lan/go2rtc", id="path"),
    ],
)
def test_go2rtc_urls_are_accepted_as_http_addresses(value: str, expected: str) -> None:
    """A go2rtc API base is an http(s) address, kept as typed apart from spaces."""
    assert validate_go2rtc_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("frigate:1984", id="no-scheme"),
        pytest.param("rtsp://frigate:8554", id="rtsp"),
        pytest.param("http://", id="no-host"),
    ],
)
def test_go2rtc_urls_without_an_http_host_are_refused(value: str) -> None:
    """Anything go2rtc's HTTP API cannot be reached at is refused."""
    with pytest.raises(ValueError):
        validate_go2rtc_url(value)


async def test_start_registers_every_feed_with_its_whep_source(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """go2rtc gets one in-memory stream per camera, pulling from this HA's WHEP view."""
    _mock_streams(aioclient_mock, {})
    restreamer = make_restreamer(hass, make_yuka_cameras(hass))

    await _start(hass, restreamer)

    assert _patches(aioclient_mock) == {
        name: whep_source(restreamer, name) for name in _ALL_STREAMS
    }


async def test_feed_tokens_differ_per_feed(hass: HomeAssistant) -> None:
    """A token leaked from one stream's source must not open another feed."""
    restreamer = make_restreamer(hass, make_yuka_cameras(hass))

    tokens = {feed.token for feed in restreamer.feeds.values()}

    assert len(tokens) == len(_ALL_STREAMS)


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(idle_stream, id="idle"),
        pytest.param(pulled_stream, id="pulled"),
    ],
)
async def test_feeds_go2rtc_already_holds_are_left_alone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    render: Callable[[str], dict[str, Any]],
) -> None:
    """Re-patching a stream go2rtc is pulling would restart its producer."""
    restreamer = make_restreamer(hass, make_yuka_cameras(hass))
    _mock_streams(
        aioclient_mock,
        {name: render(whep_source(restreamer, name)) for name in _ALL_STREAMS},
    )

    await _start(hass, restreamer)

    assert _patches(aioclient_mock) == {}


async def test_a_stream_left_from_an_earlier_run_is_repointed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """After an HA restart go2rtc still holds the old token, which no longer works."""
    restreamer = make_restreamer(hass, make_yuka_cameras(hass))
    streams = {
        name: idle_stream(whep_source(restreamer, name)) for name in _ALL_STREAMS
    }
    streams[STREAM_RIGHT] = idle_stream(
        f"webrtc:{HA_INTERNAL_URL}/api/mammotion/whep/{STREAM_RIGHT}?token=stale"
    )
    _mock_streams(aioclient_mock, streams)

    await _start(hass, restreamer)

    assert _patches(aioclient_mock) == {
        STREAM_RIGHT: whep_source(restreamer, STREAM_RIGHT)
    }


async def test_a_stream_go2rtc_renders_as_null_is_not_repatched(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """go2rtc 1.9.2 renders a stream it is busy with as null; that is not "missing"."""
    restreamer = make_restreamer(hass, make_yuka_cameras(hass))
    _mock_streams(aioclient_mock, dict.fromkeys(_ALL_STREAMS))

    await _start(hass, restreamer)

    assert _patches(aioclient_mock) == {}


async def test_feeds_go2rtc_lost_are_registered_again_on_the_next_sync(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Frigate regenerates go2rtc's config on restart, dropping API-added streams."""
    restreamer = make_restreamer(hass, make_yuka_cameras(hass))
    _mock_streams(
        aioclient_mock,
        {name: idle_stream(whep_source(restreamer, name)) for name in _ALL_STREAMS},
    )
    await _start(hass, restreamer)
    _mock_streams(aioclient_mock, {})

    await _sync(hass, freezer)

    assert set(_patches(aioclient_mock)) == set(_ALL_STREAMS)


async def test_credentials_in_the_go2rtc_url_stay_out_of_the_request_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """User and password go as HTTP basic auth, not in the URL go2rtc logs."""
    _mock_streams(aioclient_mock, {})
    restreamer = make_restreamer(
        hass, make_yuka_cameras(hass), "http://admin:secret@frigate:1984/"
    )

    await _start(hass, restreamer)

    assert {url.with_query(None) for _, url, *_ in aioclient_mock.mock_calls} == {
        URL(GO2RTC_STREAMS)
    }
    assert {headers["Authorization"] for *_, headers in aioclient_mock.mock_calls} == {
        encode_basic_auth("admin", "secret")
    }


async def test_an_unreachable_go2rtc_warns_once_per_outage(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Frigate being down must not fill the log every 30 seconds."""
    aioclient_mock.get(GO2RTC_STREAMS, exc=ClientError("connection refused"))
    restreamer = make_restreamer(hass, make_yuka_cameras(hass))

    await _start(hass, restreamer)
    await _sync(hass, freezer)
    await _sync(hass, freezer)

    assert (
        _warnings(caplog).count(
            "Cannot reach go2rtc at http://frigate:1984 to register the camera "
            "re-streams: connection refused"
        )
        == 1
    )


async def test_a_feed_go2rtc_stopped_pulling_is_closed_after_two_idle_syncs(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    sessions: list[FakeAgoraSession],
) -> None:
    """go2rtc never sends a WHEP DELETE, so its silence is what ends the mower stream."""
    coordinator = make_camera_coordinator()
    cameras = make_yuka_cameras(hass, coordinator)
    restreamer = make_restreamer(hass, cameras)
    idle = {name: idle_stream(whep_source(restreamer, name)) for name in _ALL_STREAMS}
    _mock_streams(aioclient_mock, idle)
    await _start(hass, restreamer)
    feed = restreamer.feeds[STREAM_LEFT]
    feed.session_id, _ = await cameras[0].async_handle_whep_offer(OFFER_SDP)

    await _sync(hass, freezer)
    closed_after_one = sessions[0].close_calls
    await _sync(hass, freezer)

    assert closed_after_one == 0, "one idle sync must not close a feed still joining"
    assert sessions[0].close_calls == 1
    coordinator.async_release_camera_session.assert_awaited_once_with("webrtc_camera")


async def test_a_feed_go2rtc_keeps_pulling_stays_open(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    sessions: list[FakeAgoraSession],
) -> None:
    """A stream with a consumer is in use, however many syncs pass."""
    cameras = make_yuka_cameras(hass)
    restreamer = make_restreamer(hass, cameras)
    pulled = {
        name: pulled_stream(whep_source(restreamer, name)) for name in _ALL_STREAMS
    }
    _mock_streams(aioclient_mock, pulled)
    await _start(hass, restreamer)
    feed = restreamer.feeds[STREAM_LEFT]
    feed.session_id, _ = await cameras[0].async_handle_whep_offer(OFFER_SDP)

    for _ in range(3):
        await _sync(hass, freezer)

    assert sessions[0].close_calls == 0


async def test_stop_withdraws_the_feeds_and_closes_their_sessions(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    sessions: list[FakeAgoraSession],
) -> None:
    """On unload the streams leave go2rtc and the WHEP view stops answering for them."""
    cameras = make_yuka_cameras(hass)
    restreamer = make_restreamer(hass, cameras)
    _mock_streams(aioclient_mock, {})
    await _start(hass, restreamer)
    feed = restreamer.feeds[STREAM_LEFT]
    feed.session_id, _ = await cameras[0].async_handle_whep_offer(OFFER_SDP)
    aioclient_mock.clear_requests()
    aioclient_mock.delete(GO2RTC_STREAMS)

    await restreamer.async_stop()
    await hass.async_block_till_done()

    deleted = {url.query["src"] for method, url, *_ in aioclient_mock.mock_calls}
    assert deleted == set(_ALL_STREAMS)
    assert hass.data[DATA_WHEP_FEEDS] == {}
    assert sessions[0].close_calls == 1


async def test_a_refused_registration_is_logged_with_go2rtc_reason(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """go2rtc's own words are the only clue why Frigate shows no stream."""
    aioclient_mock.get(GO2RTC_STREAMS, json={})
    aioclient_mock.patch(
        GO2RTC_STREAMS, status=400, text="streams: source not supported\n"
    )
    restreamer = make_restreamer(hass, make_yuka_cameras(hass)[:1])

    await _start(hass, restreamer)

    assert _warnings(caplog) == [
        f"go2rtc refused PATCH of {STREAM_LEFT}: HTTP 400 streams: source not supported"
    ]


async def test_without_a_url_for_home_assistant_nothing_is_registered(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A source go2rtc cannot dial is worse than none; say so once."""
    await hass.config.async_update(internal_url=None, external_url=None)
    _mock_streams(aioclient_mock, {})
    restreamer = make_restreamer(hass, make_yuka_cameras(hass)[:1])

    with patch(
        "custom_components.mammotion.restream.get_url",
        side_effect=NoURLAvailableError,
    ):
        await _start(hass, restreamer)
        await _sync(hass, freezer)

    assert _patches(aioclient_mock) == {}
    assert len(_warnings(caplog)) == 1
