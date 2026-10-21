"""Re-stream the camera feeds through an external go2rtc server, such as Frigate's.

The camera entities answer WebRTC offers natively (pyagorartc), so Home
Assistant's own go2rtc never handles them and there is no RTSP URL to give an
NVR.  Instead each feed gets a WHEP endpoint here, and the go2rtc server named
in the options is told to pull from it.  That server then serves the feed as
``rtsp://<go2rtc>:8554/<stream name>`` to anything that asks, Frigate included.

The stream is registered with ``PATCH /api/streams`` so it lives only in that
server's memory: the token in its source URL is valid for this Home Assistant
run only, and a periodic sync puts it back after either side restarts.  go2rtc
never sends a WHEP ``DELETE`` when its last consumer leaves, so the same sync
closes a feed's session once go2rtc has had no consumer for it for a while.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError, ClientTimeout, encode_basic_auth, web
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import slugify
from homeassistant.util.hass_dict import HassKey
from yarl import URL

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .camera import MammotionWebRTCCamera

_LOGGER = logging.getLogger(__name__)

GO2RTC_SYNC_INTERVAL = timedelta(seconds=30)
#: Syncs in a row that must find no go2rtc consumer before a feed is closed.
IDLE_SYNCS_BEFORE_CLOSE = 2
WHEP_PATH = "/api/mammotion/whep"
#: Session ids of WHEP offers, so the camera can tell go2rtc from a frontend viewer.
WHEP_SESSION_PREFIX = "whep-"
#: Seconds a feed refuses WHEP offers after consecutive failed joins.  Frigate
#: restarts ffmpeg every few seconds, and each offer asks Mammotion's cloud for
#: a stream token and wakes the mower.
WHEP_RETRY_BACKOFF: tuple[int, ...] = (30, 60, 120, 300, 600)
_API_PATH = "api/streams"
_API_TIMEOUT = ClientTimeout(total=10)
_SDP_CONTENT_TYPE = "application/sdp"

_CODEC_LINE = re.compile(r"^a=(?:rtpmap|fmtp|rtcp-fb):(?P<pt>\d+) ")
_RTX_FMTP = re.compile(r"^a=fmtp:(?P<pt>\d+) (?:.*;)?apt=(?P<apt>\d+)(?:;|$)")

_DATA_VIEWS_REGISTERED: HassKey[bool] = HassKey(f"{DOMAIN}_whep_views")


class WhepOfferError(Exception):
    """A WHEP offer the camera could not answer, carrying the HTTP status to send."""

    def __init__(self, status: int, message: str) -> None:
        """Keep the status and the camera's reason."""
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class WhepFeed:
    """One camera feed exposed over WHEP, and its session while go2rtc pulls it."""

    camera: MammotionWebRTCCamera
    token: str
    session_id: str | None = None
    idle_syncs: int = 0
    failed_joins: int = 0
    retry_at: float = 0.0


#: Feeds that may be pulled over WHEP, by go2rtc stream name, across entries.
DATA_WHEP_FEEDS: HassKey[dict[str, WhepFeed]] = HassKey(f"{DOMAIN}_whep_feeds")


def go2rtc_stream_name(device_name: str, camera_key: str) -> str:
    """Return the stable go2rtc stream name for one camera of one mower."""
    return f"{DOMAIN}_{slugify(device_name)}_{camera_key.removeprefix('webrtc_')}"


def validate_go2rtc_url(value: str) -> str:
    """Return *value* as a go2rtc API base URL, or raise ``ValueError``."""
    url = URL(value.strip())
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError(value)
    return str(url)


def keep_relayed_video_codec(answer_sdp: str, payload_type: int | None) -> str:
    """Return *answer_sdp* with its video section narrowed to *payload_type*.

    The gateway's answer lists every video codec the offer and the gateway share,
    but it relays the mower's stream on one of them (H265 on the mowers seen so
    far).  go2rtc binds its consumers to the first codec of the answer, so with
    H264 listed first the H265 packets reach no one.  The relayed codec's RTX
    payload type is kept with it.  An unknown or absent *payload_type* (the
    gateway's ``pt`` 0) leaves the answer unchanged.
    """
    if not payload_type:
        return answer_sdp
    lines = answer_sdp.split("\r\n")
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("m=video ")), None
    )
    if start is None:
        return answer_sdp
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("m=")),
        len(lines),
    )
    m_line = lines[start].split(" ")
    offered = m_line[3:]
    if str(payload_type) not in offered:
        return answer_sdp
    keep = {str(payload_type)} | {
        match["pt"]
        for line in lines[start:end]
        if (match := _RTX_FMTP.match(line)) and match["apt"] == str(payload_type)
    }
    section = [" ".join([*m_line[:3], *(pt for pt in offered if pt in keep)])]
    for line in lines[start + 1 : end]:
        match = _CODEC_LINE.match(line)
        if match is None or match["pt"] in keep:
            section.append(line)
    return "\r\n".join([*lines[:start], *section, *lines[end:]])


def _producer_urls(stream: dict[str, Any]) -> set[str]:
    """Return every producer source of a go2rtc stream, with its ``webrtc:`` prefix.

    An idle producer reports its configured source; a running WHEP one reports
    the bare HTTP URL it dialled.
    """
    urls: set[str] = set()
    for producer in stream.get("producers") or ():
        if isinstance(producer, dict) and isinstance(url := producer.get("url"), str):
            urls.add(url if url.startswith("webrtc:") else f"webrtc:{url}")
    return urls


class Go2RtcRestreamer:
    """Keep one config entry's camera feeds registered on a go2rtc server."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        go2rtc_url: str,
        cameras: list[MammotionWebRTCCamera],
    ) -> None:
        """Mint a WHEP token per feed; nothing is registered until started."""
        self.hass = hass
        self._entry = entry
        url = URL(go2rtc_url)
        self._headers = (
            {"Authorization": encode_basic_auth(url.user, url.password or "")}
            if url.user is not None
            else {}
        )
        self._api_url = url.with_user(None).with_password(None).with_query(None)
        self._api_url = self._api_url.with_path(
            f"{self._api_url.path.rstrip('/')}/{_API_PATH}"
        )
        self.feeds: dict[str, WhepFeed] = {
            go2rtc_stream_name(
                camera.coordinator.device.device_name, camera.entity_description.key
            ): WhepFeed(camera=camera, token=secrets.token_urlsafe(32))
            for camera in cameras
        }
        self._cancel_interval: CALLBACK_TYPE | None = None
        self._unreachable_logged = False
        self._no_url_logged = False

    async def async_start(self) -> None:
        """Expose the feeds and register them with go2rtc now and on every sync."""
        if not self.hass.data.get(_DATA_VIEWS_REGISTERED):
            self.hass.http.register_view(MammotionWhepView())
            self.hass.http.register_view(MammotionWhepSessionView())
            self.hass.data[_DATA_VIEWS_REGISTERED] = True
        self.hass.data.setdefault(DATA_WHEP_FEEDS, {}).update(self.feeds)
        self._cancel_interval = async_track_time_interval(
            self.hass,
            self._async_sync,
            GO2RTC_SYNC_INTERVAL,
            name=f"{DOMAIN} go2rtc sync",
            cancel_on_shutdown=True,
        )
        self._entry.async_create_background_task(
            self.hass, self._async_sync(), f"{DOMAIN} go2rtc register"
        )

    async def async_stop(self) -> None:
        """Withdraw the feeds: no more offers, open sessions closed, streams removed."""
        if self._cancel_interval is not None:
            self._cancel_interval()
            self._cancel_interval = None
        registered = self.hass.data.get(DATA_WHEP_FEEDS, {})
        for name, feed in self.feeds.items():
            if registered.get(name) is feed:
                del registered[name]
            self._close_feed(feed)
        for name in self.feeds:
            # go2rtc also rewrites its config file on delete and may refuse that
            # part (Frigate's is generated); the in-memory stream is gone either way.
            await self._async_call("delete", {"src": name}, warn=False)

    def whep_source(self, name: str) -> str | None:
        """Return the go2rtc source that pulls feed *name* from this Home Assistant."""
        try:
            base_url = get_url(self.hass, allow_external=False)
        except NoURLAvailableError:
            try:
                base_url = get_url(self.hass)
            except NoURLAvailableError:
                return None
        query = f"token={self.feeds[name].token}"
        return f"webrtc:{base_url.rstrip('/')}{WHEP_PATH}/{name}?{query}"

    async def _async_sync(self, _now: datetime | None = None) -> None:
        """Re-register any feed go2rtc lost and close feeds go2rtc stopped pulling."""
        streams = await self._async_get_streams()
        if streams is None:
            return
        for name, feed in self.feeds.items():
            if name not in streams:
                await self._async_register(name)
                self._note_idle(name, feed)
                continue
            # go2rtc 1.9.2 renders a stream it cannot lock as null; judge it next sync.
            if (stream := streams[name]) is None:
                continue
            if (source := self.whep_source(name)) not in _producer_urls(stream):
                await self._async_register(name, source)
            if stream.get("consumers"):
                feed.idle_syncs = 0
            else:
                self._note_idle(name, feed)

    def _note_idle(self, name: str, feed: WhepFeed) -> None:
        """Count a sync without consumers; close the feed's session after enough."""
        if feed.session_id is None:
            feed.idle_syncs = 0
            return
        feed.idle_syncs += 1
        if feed.idle_syncs >= IDLE_SYNCS_BEFORE_CLOSE:
            _LOGGER.debug("go2rtc stopped pulling %s; closing its stream", name)
            self._close_feed(feed)

    @callback
    def _close_feed(self, feed: WhepFeed) -> None:
        """Close the feed's WHEP session, if it has one."""
        if (session_id := feed.session_id) is not None:
            feed.session_id = None
            feed.idle_syncs = 0
            feed.camera.close_webrtc_session(session_id)

    async def _async_register(self, name: str, source: str | None = None) -> None:
        """Point go2rtc's stream *name* at this feed's WHEP endpoint."""
        if source is None and (source := self.whep_source(name)) is None:
            if not self._no_url_logged:
                self._no_url_logged = True
                _LOGGER.warning(
                    "Cannot register %s with go2rtc: Home Assistant has no URL "
                    "configured that go2rtc could reach",
                    name,
                )
            return
        if await self._async_call("patch", {"name": name, "src": source}):
            _LOGGER.debug("Registered %s with go2rtc", name)

    async def _async_get_streams(self) -> dict[str, dict[str, Any] | None] | None:
        """Return go2rtc's streams, or None when it cannot be asked."""
        try:
            async with async_get_clientsession(self.hass).get(
                self._api_url, headers=self._headers, timeout=_API_TIMEOUT
            ) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as err:
            self._log_unreachable(err)
            return None
        if not isinstance(payload, dict):
            self._log_unreachable(TypeError(f"unexpected reply {type(payload)}"))
            return None
        if self._unreachable_logged:
            _LOGGER.info("go2rtc at %s is reachable again", self._api_url.origin())
            self._unreachable_logged = False
        return payload

    async def _async_call(
        self, method: str, params: dict[str, str], *, warn: bool = True
    ) -> bool:
        """Send one go2rtc streams API request; return whether it succeeded."""
        try:
            async with async_get_clientsession(self.hass).request(
                method,
                self._api_url,
                params=params,
                headers=self._headers,
                timeout=_API_TIMEOUT,
            ) as response:
                if response.status >= HTTPStatus.BAD_REQUEST:
                    (_LOGGER.warning if warn else _LOGGER.debug)(
                        "go2rtc refused %s of %s: HTTP %s %s",
                        method.upper(),
                        params.get("name") or params.get("src"),
                        response.status,
                        (await response.text()).strip(),
                    )
                    return False
        except (ClientError, TimeoutError) as err:
            self._log_unreachable(err)
            return False
        return True

    def _log_unreachable(self, err: Exception) -> None:
        """Warn once per outage that go2rtc cannot be reached, then stay quiet."""
        if self._unreachable_logged:
            _LOGGER.debug("go2rtc still unreachable: %s", err)
            return
        self._unreachable_logged = True
        _LOGGER.warning(
            "Cannot reach go2rtc at %s to register the camera re-streams: %s",
            self._api_url.origin(),
            err,
        )


def _feed_for(request: web.Request, stream_name: str) -> WhepFeed | web.Response:
    """Return the feed a request may use, or the error response to send instead."""
    feed = request.app[KEY_HASS].data.get(DATA_WHEP_FEEDS, {}).get(stream_name)
    if feed is None:
        return web.Response(status=HTTPStatus.NOT_FOUND, text="Unknown stream")
    if not hmac.compare_digest(request.query.get("token", ""), feed.token):
        return web.Response(status=HTTPStatus.UNAUTHORIZED, text="Invalid token")
    return feed


class MammotionWhepView(HomeAssistantView):
    """WHEP endpoint go2rtc pulls one camera feed from."""

    url = f"{WHEP_PATH}/{{stream_name}}"
    name = "api:mammotion:whep"
    # go2rtc cannot send Home Assistant credentials; the feed's token authorises.
    requires_auth = False

    async def post(self, request: web.Request, stream_name: str) -> web.Response:
        """Answer go2rtc's SDP offer by joining the feed's Agora channel."""
        feed = _feed_for(request, stream_name)
        if isinstance(feed, web.Response):
            return feed
        offer_sdp = await request.text()
        if not offer_sdp.strip():
            return web.Response(status=HTTPStatus.BAD_REQUEST, text="Empty SDP offer")
        if (wait := feed.retry_at - time.monotonic()) > 0:
            return web.Response(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                text="Camera unavailable; retrying later",
                headers={"Retry-After": str(int(wait) + 1)},
            )
        try:
            session_id, answer_sdp = await feed.camera.async_handle_whep_offer(
                offer_sdp, replaces=feed.session_id
            )
        except WhepOfferError as err:
            # A conflict is another join still running, not this feed failing.
            if err.status != HTTPStatus.CONFLICT:
                backoff = WHEP_RETRY_BACKOFF[
                    min(feed.failed_joins, len(WHEP_RETRY_BACKOFF) - 1)
                ]
                feed.failed_joins += 1
                feed.retry_at = time.monotonic() + backoff
            return web.Response(status=err.status, text=err.message)
        feed.session_id = session_id
        feed.idle_syncs = 0
        feed.failed_joins = 0
        return web.Response(
            status=HTTPStatus.CREATED,
            text=answer_sdp,
            content_type=_SDP_CONTENT_TYPE,
            headers={"Location": f"{request.path}/{session_id}"},
        )


class MammotionWhepSessionView(HomeAssistantView):
    """WHEP session resource, so a client that says goodbye can end the stream."""

    url = f"{WHEP_PATH}/{{stream_name}}/{{session_id}}"
    name = "api:mammotion:whep:session"
    requires_auth = False

    async def delete(
        self, request: web.Request, stream_name: str, session_id: str
    ) -> web.Response:
        """Close the feed's session if *session_id* is still the current one."""
        feed = _feed_for(request, stream_name)
        if isinstance(feed, web.Response):
            return feed
        if feed.session_id != session_id:
            return web.Response(status=HTTPStatus.NOT_FOUND, text="No such session")
        feed.session_id = None
        feed.idle_syncs = 0
        feed.camera.close_webrtc_session(session_id)
        return web.Response(status=HTTPStatus.OK)
