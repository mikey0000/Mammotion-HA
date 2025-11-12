"""Agora RTC capabilities - Complete codec and extension definitions.

This module contains the complete list of codecs and RTP extensions
as defined by the Agora WebRTC SDK v4.24.0.
"""

from typing import Any


def get_audio_codecs() -> list[dict[str, Any]]:
    """Get complete audio codec list (sendrecv)."""
    return [
        {
            "payloadType": 111,
            "rtpMap": {
                "encodingName": "opus",
                "clockRate": 48000,
                "encodingParameters": 2,
            },
            "rtcpFeedbacks": [
                {"type": "transport-cc"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "minptime": "10",
                    "useinbandfec": "1",
                }
            },
        },
        {
            "payloadType": 63,
            "rtpMap": {
                "encodingName": "red",
                "clockRate": 48000,
                "encodingParameters": 2,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "111/111": None,
                }
            },
        },
        {
            "payloadType": 9,
            "rtpMap": {
                "encodingName": "G722",
                "clockRate": 8000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
        },
        {
            "payloadType": 0,
            "rtpMap": {
                "encodingName": "PCMU",
                "clockRate": 8000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
        },
        {
            "payloadType": 8,
            "rtpMap": {
                "encodingName": "PCMA",
                "clockRate": 8000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
        },
        {
            "payloadType": 13,
            "rtpMap": {
                "encodingName": "CN",
                "clockRate": 8000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
        },
        {
            "payloadType": 110,
            "rtpMap": {
                "encodingName": "telephone-event",
                "clockRate": 48000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
        },
        {
            "payloadType": 126,
            "rtpMap": {
                "encodingName": "telephone-event",
                "clockRate": 8000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
        },
    ]


def get_video_codecs_sendrecv() -> list[dict[str, Any]]:
    """Get video codecs for sendrecv."""
    return [
        # VP8
        {
            "payloadType": 96,
            "rtpMap": {
                "encodingName": "VP8",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
        },
        {
            "payloadType": 97,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "96",
                }
            },
        },
        # H264 - Baseline (42001f) - packetization-mode 1
        {
            "payloadType": 103,
            "rtpMap": {
                "encodingName": "H264",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-asymmetry-allowed": "1",
                    "packetization-mode": "1",
                    "profile-level-id": "42001f",
                }
            },
        },
        {
            "payloadType": 104,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "103",
                }
            },
        },
        # H264 - Baseline (42001f) - packetization-mode 0
        {
            "payloadType": 107,
            "rtpMap": {
                "encodingName": "H264",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-asymmetry-allowed": "1",
                    "packetization-mode": "0",
                    "profile-level-id": "42001f",
                }
            },
        },
        {
            "payloadType": 108,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "107",
                }
            },
        },
        # H264 - Constrained Baseline (42e01f) - packetization-mode 1
        {
            "payloadType": 109,
            "rtpMap": {
                "encodingName": "H264",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-asymmetry-allowed": "1",
                    "packetization-mode": "1",
                    "profile-level-id": "42e01f",
                }
            },
        },
        {
            "payloadType": 114,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "109",
                }
            },
        },
        # H264 - Constrained Baseline (42e01f) - packetization-mode 0
        {
            "payloadType": 115,
            "rtpMap": {
                "encodingName": "H264",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-asymmetry-allowed": "1",
                    "packetization-mode": "0",
                    "profile-level-id": "42e01f",
                }
            },
        },
        {
            "payloadType": 116,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "115",
                }
            },
        },
        # H264 - Main (4d001f) - packetization-mode 1
        {
            "payloadType": 117,
            "rtpMap": {
                "encodingName": "H264",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-asymmetry-allowed": "1",
                    "packetization-mode": "1",
                    "profile-level-id": "4d001f",
                }
            },
        },
        {
            "payloadType": 118,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "117",
                }
            },
        },
        # H264 - Main (4d001f) - packetization-mode 0
        {
            "payloadType": 39,
            "rtpMap": {
                "encodingName": "H264",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-asymmetry-allowed": "1",
                    "packetization-mode": "0",
                    "profile-level-id": "4d001f",
                }
            },
        },
        {
            "payloadType": 40,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "39",
                }
            },
        },
        # AV1 - Profile 0
        {
            "payloadType": 45,
            "rtpMap": {
                "encodingName": "AV1",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-idx": "5",
                    "profile": "0",
                    "tier": "0",
                }
            },
        },
        {
            "payloadType": 46,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "45",
                }
            },
        },
        # VP9 - Profile 0
        {
            "payloadType": 98,
            "rtpMap": {
                "encodingName": "VP9",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "profile-id": "0",
                }
            },
        },
        {
            "payloadType": 99,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "98",
                }
            },
        },
        # VP9 - Profile 2
        {
            "payloadType": 100,
            "rtpMap": {
                "encodingName": "VP9",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "profile-id": "2",
                }
            },
        },
        {
            "payloadType": 101,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "100",
                }
            },
        },
    ]


def get_video_codecs_recv() -> list[dict[str, Any]]:
    """Get additional video codecs for recv only (VP9 1/3, AV1 1, H265)."""
    return [
        # VP9 - Profile 1
        {
            "payloadType": 35,
            "rtpMap": {
                "encodingName": "VP9",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "profile-id": "1",
                }
            },
        },
        {
            "payloadType": 36,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "35",
                }
            },
        },
        # VP9 - Profile 3
        {
            "payloadType": 37,
            "rtpMap": {
                "encodingName": "VP9",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "profile-id": "3",
                }
            },
        },
        {
            "payloadType": 38,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "37",
                }
            },
        },
        # AV1 - Profile 1
        {
            "payloadType": 47,
            "rtpMap": {
                "encodingName": "AV1",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-idx": "5",
                    "profile": "1",
                    "tier": "0",
                }
            },
        },
        {
            "payloadType": 48,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "47",
                }
            },
        },
        # H265 (HEVC) - Profile 1
        {
            "payloadType": 49,
            "rtpMap": {
                "encodingName": "H265",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-id": "180",
                    "profile-id": "1",
                    "tier-flag": "0",
                    "tx-mode": "SRST",
                }
            },
        },
        {
            "payloadType": 50,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "49",
                }
            },
        },
        # H265 (HEVC) - Profile 2
        {
            "payloadType": 51,
            "rtpMap": {
                "encodingName": "H265",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "goog-remb"},
                {"type": "transport-cc"},
                {"type": "ccm", "parameter": "fir"},
                {"type": "nack"},
                {"type": "nack", "parameter": "pli"},
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "level-id": "180",
                    "profile-id": "2",
                    "tier-flag": "0",
                    "tx-mode": "SRST",
                }
            },
        },
        {
            "payloadType": 52,
            "rtpMap": {
                "encodingName": "rtx",
                "clockRate": 90000,
            },
            "rtcpFeedbacks": [
                {"type": "rrtr"},
            ],
            "fmtp": {
                "parameters": {
                    "apt": "51",
                }
            },
        },
    ]


def get_audio_extensions() -> list[dict[str, Any]]:
    """Get audio RTP header extensions with fixed entry IDs."""
    return [
        {
            "entry": 14,
            "extensionName": "urn:ietf:params:rtp-hdrext:ssrc-audio-level",
        },
        {
            "entry": 2,
            "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
        },
        {
            "entry": 4,
            "extensionName": "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
        },
        {
            "entry": 9,
            "extensionName": "urn:ietf:params:rtp-hdrext:sdes:mid",
        },
    ]


def get_video_extensions() -> list[dict[str, Any]]:
    """Get video RTP header extensions with fixed entry IDs."""
    return [
        {
            "entry": 1,
            "extensionName": "urn:ietf:params:rtp-hdrext:toffset",
        },
        {
            "entry": 2,
            "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
        },
        {
            "entry": 3,
            "extensionName": "urn:3gpp:video-orientation",
        },
        {
            "entry": 4,
            "extensionName": "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
        },
        {
            "entry": 5,
            "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/playout-delay",
        },
        {
            "entry": 6,
            "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/video-content-type",
        },
        {
            "entry": 7,
            "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/video-timing",
        },
        {
            "entry": 8,
            "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/color-space",
        },
        {
            "entry": 9,
            "extensionName": "urn:ietf:params:rtp-hdrext:sdes:mid",
        },
        {
            "entry": 10,
            "extensionName": "urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id",
        },
        {
            "entry": 11,
            "extensionName": "urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id",
        },
    ]
