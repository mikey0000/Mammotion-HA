"""The answer go2rtc gets: narrowed to the codec the gateway relays the mower on.

``fixtures/go2rtc_whep_offer.sdp`` is CAPTURED: the offer go2rtc 1.9.9 POSTed to
the WHEP view, with only the trailing spaces pion leaves after ``rtcp-fb`` removed.
``fixtures/gateway_ortc_go2rtc.json`` is RECONSTRUCTED in the shape
of pyagorartc's trimmed real ``join_v3`` ORTC, listing the go2rtc offer's H264 and
H265 plus an RTX for H265.  The answer is built by pyagorartc itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agora_session_support import (
    FakeAgoraSession,
    install_fake_sessions,
    make_camera,
    make_camera_coordinator,
)
from homeassistant.core import HomeAssistant
from pyagorartc import RemoteStream, SessionOptions
from pyagorartc.sdp import answer_from_ortc

from custom_components.mammotion.restream import keep_relayed_video_codec

_FIXTURES = Path(__file__).parent / "fixtures"
_H265 = 100
_H265_RTX = 99


def _offer() -> str:
    """Return the captured offer with the CRLF line ends it had on the wire."""
    # Stored with LF: git am and editors would strip the CRs anyway.
    sdp = (_FIXTURES / "go2rtc_whep_offer.sdp").read_text()
    return sdp.replace("\n", "\r\n")


def _answer() -> str:
    """Answer the captured go2rtc offer as pyagorartc does for a WHEP session."""
    gateway_ortc = json.loads((_FIXTURES / "gateway_ortc_go2rtc.json").read_text())
    return answer_from_ortc(
        gateway_ortc,
        _offer(),
        options=SessionOptions(declare_remote_video_ssrc=True, disable_audio=True),
        remote_video=RemoteStream(
            uid=1, ssrc=40000, rtx_ssrc=40001, payload_type=_H265
        ),
    )


def _section(sdp: str, kind: str) -> list[str]:
    lines = sdp.split("\r\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(f"m={kind} "))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("m=")),
        len(lines),
    )
    return lines[start:end]


def test_the_video_section_offers_only_the_relayed_codec_and_its_rtx() -> None:
    """go2rtc binds to the answer's first video codec, which would be H264."""
    video = _section(keep_relayed_video_codec(_answer(), _H265), "video")

    assert video[0] == f"m=video 9 UDP/TLS/RTP/SAVPF {_H265} {_H265_RTX}"
    assert [line for line in video if line.startswith("a=rtpmap:")] == [
        f"a=rtpmap:{_H265} H265/90000",
        f"a=rtpmap:{_H265_RTX} rtx/90000",
    ]
    assert not [line for line in video if ":96 " in line]


def test_everything_but_the_dropped_codecs_is_kept() -> None:
    """Transport, SSRC declarations and the audio section reach go2rtc unchanged."""
    answer = _answer()
    trimmed = keep_relayed_video_codec(answer, _H265)

    dropped = set(answer.split("\r\n")) - set(trimmed.split("\r\n"))

    assert _section(trimmed, "audio") == _section(answer, "audio")
    assert all(":96 " in line or line.startswith("m=video") for line in dropped)
    assert "a=ssrc-group:FID 40000 40001" in trimmed


@pytest.mark.parametrize(
    "payload_type",
    [
        pytest.param(None, id="no-stream"),
        pytest.param(0, id="no-codec-match"),
        pytest.param(49, id="not-in-answer"),
    ],
)
def test_an_answer_is_left_alone_without_a_known_relayed_codec(
    payload_type: int | None,
) -> None:
    """Narrowing to a codec the answer does not list would leave go2rtc with none."""
    answer = _answer()

    assert keep_relayed_video_codec(answer, payload_type) == answer


async def test_a_whep_session_waits_for_the_stream_and_answers_audio_inactive(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pion routes on the declared SSRC, so the answer must wait for the stream."""
    sessions = install_fake_sessions(monkeypatch)

    await make_camera(hass, make_camera_coordinator()).async_handle_whep_offer(_offer())

    options: SessionOptions = sessions[0].kwargs["options"]
    assert (options.declare_remote_video_ssrc, options.disable_audio) == (True, True)


async def test_a_whep_answer_is_narrowed_to_the_announced_stream(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The camera narrows the WHEP answer to the payload type the gateway announced."""
    answer = _answer()
    sessions: list[FakeAgoraSession] = install_fake_sessions(
        monkeypatch,
        answer=answer,
        remote_streams=(RemoteStream(uid=1, ssrc=40000, payload_type=_H265),),
    )
    camera = make_camera(hass, make_camera_coordinator())

    _, answered = await camera.async_handle_whep_offer(_offer())

    assert answered == keep_relayed_video_codec(answer, _H265)
    assert sessions[0].joined_with is not None
