"""Agora SDP manipulation logic mimicking agoraRTC_N.js."""

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class SDPParser:
    """Basic SDP parser to avoid external dependencies, matching Agora JS behavior."""

    @staticmethod
    def parse(sdp: str) -> dict[str, Any]:
        parsed = {"media": []}
        current_media = None

        for line in sdp.splitlines():
            line = line.strip()
            if not line:
                continue

            parts = line.split("=", 1)
            if len(parts) < 2:
                continue
            ltype, lval = parts

            if ltype == "v":
                parsed["version"] = lval
            elif ltype == "o":
                oparts = lval.split()
                if len(oparts) >= 6:
                    parsed["origin"] = {
                        "username": oparts[0],
                        "sessionId": oparts[1],
                        "sessionVersion": oparts[2],
                        "netType": oparts[3],
                        "ipVer": oparts[4],
                        "address": oparts[5],
                    }
            elif ltype == "s":
                parsed["name"] = lval
            elif ltype == "m":
                mparts = lval.split()
                current_media = {
                    "type": mparts[0],
                    "port": int(mparts[1]),
                    "protocol": mparts[2],
                    "payloads": " ".join(mparts[3:]),
                    "rtp": [],
                    "fmtp": [],
                    "rtcpFb": [],
                    "ext": [],
                    "fingerprints": [],
                    "attributes": {},
                }
                parsed["media"].append(current_media)
            elif ltype == "a":
                aparts = lval.split(":", 1)
                attr = aparts[0]
                val = aparts[1] if len(aparts) > 1 else None

                target = current_media if current_media else parsed

                if attr == "ice-ufrag":
                    target["iceUfrag"] = val
                elif attr == "ice-pwd":
                    target["icePwd"] = val
                elif attr == "fingerprint":
                    fparts = val.split()
                    fp_obj = {"hash": fparts[0], "fingerprint": fparts[1]}
                    target["fingerprints"] = target.get("fingerprints", [])
                    target["fingerprints"].append(fp_obj)
                    # Keep backward compatibility for 'fingerprint' key if needed by other code?
                    # But parse_offer_to_ortc should use fingerprints list.
                    target["fingerprint"] = fp_obj
                elif attr == "setup":
                    target["setup"] = val
                elif attr == "mid":
                    target["mid"] = val
                elif attr == "direction":
                    target["direction"] = val
                elif attr == "ice-options":
                    target["iceOptions"] = val
                elif attr == "rtpmap":
                    rparts = val.split(None, 1)
                    pt = int(rparts[0])
                    rmap = rparts[1].split("/")
                    target["rtp"].append(
                        {
                            "payload": pt,
                            "codec": rmap[0],
                            "rate": int(rmap[1]) if len(rmap) > 1 else 90000,
                            "encoding": rmap[2] if len(rmap) > 2 else None,
                        }
                    )
                elif attr == "fmtp":
                    fparts = val.split(None, 1)
                    target["fmtp"].append(
                        {"payload": int(fparts[0]), "config": fparts[1]}
                    )
                elif attr == "rtcp-fb":
                    fbparts = val.split()
                    target["rtcpFb"].append(
                        {
                            "payload": int(fbparts[0]),
                            "type": fbparts[1],
                            "subtype": " ".join(fbparts[2:])
                            if len(fbparts) > 2
                            else None,
                        }
                    )
                elif attr == "extmap":
                    eparts = val.split()
                    target["ext"].append({"value": int(eparts[0]), "uri": eparts[1]})
                elif attr == "group":
                    if "groups" not in parsed:
                        parsed["groups"] = []
                    gparts = val.split()
                    parsed["groups"].append(
                        {"type": gparts[0], "mids": " ".join(gparts[1:])}
                    )
                elif attr == "msid-semantic":
                    parsed["msidSemantic"] = {
                        "semantic": val.split()[0],
                        "token": val.split()[1] if len(val.split()) > 1 else "",
                    }
        return parsed

    @staticmethod
    def write(parsed: dict[str, Any]) -> str:
        lines = [f"v={parsed.get('version', 0)}"]
        orig = parsed.get("origin", {})
        lines.append(
            f"o={orig.get('username', '-')} {orig.get('sessionId', 0)} {orig.get('sessionVersion', 0)} {orig.get('netType', 'IN')} IP{orig.get('ipVer', 4)} {orig.get('address', '127.0.0.1')}"
        )
        lines.append(f"s={parsed.get('name', '-')}")
        lines.append("t=0 0")
        for g in parsed.get("groups", []):
            lines.append(f"a=group:{g['type']} {g['mids']}")
        if "msidSemantic" in parsed:
            lines.append(
                f"a=msid-semantic: {parsed['msidSemantic']['semantic']} {parsed['msidSemantic']['token']}"
            )
        if "icelite" in parsed:
            lines.append("a=ice-lite")
        if "extmapAllowMixed" in parsed:
            lines.append("a=extmap-allow-mixed")

        for m in parsed.get("media", []):
            lines.append(f"m={m['type']} {m['port']} {m['protocol']} {m['payloads']}")
            lines.append(f"c=IN IP4 {m.get('connection', {}).get('ip', '0.0.0.0')}")
            if "rtcp" in m:
                lines.append(
                    f"a=rtcp:{m['rtcp']['port']} IN IP4 {m['rtcp'].address if hasattr(m['rtcp'], 'address') else m['rtcp'].get('address', '0.0.0.0')}"
                )
            if "iceUfrag" in m:
                lines.append(f"a=ice-ufrag:{m['iceUfrag']}")
            if "icePwd" in m:
                lines.append(f"a=ice-pwd:{m['icePwd']}")
            if "iceOptions" in m:
                lines.append(f"a=ice-options:{m['iceOptions']}")
            if "fingerprint" in m and not m.get("fingerprints"):
                lines.append(
                    f"a=fingerprint:{m['fingerprint']['hash']} {m['fingerprint']['fingerprint']}"
                )
            for fp in m.get("fingerprints", []):
                lines.append(f"a=fingerprint:{fp['hash']} {fp['fingerprint']}")
            if "setup" in m:
                lines.append(f"a=setup:{m['setup']}")
            if "mid" in m:
                lines.append(f"a=mid:{m['mid']}")
            if "direction" in m:
                lines.append(f"a={m['direction']}")
            for r in m.get("rtp", []):
                val = f"{r['payload']} {r['codec']}/{r['rate']}"
                if r.get("encoding"):
                    val += f"/{r['encoding']}"
                lines.append(f"a=rtpmap:{val}")
            for fb in m.get("rtcpFb", []):
                val = f"{fb['payload']} {fb['type']}"
                if fb.get("subtype"):
                    val += f" {fb['subtype']}"
                lines.append(f"a=rtcp-fb:{val}")
            for f in m.get("fmtp", []):
                lines.append(f"a=fmtp:{f['payload']} {f['config']}")
            for e in m.get("ext", []):
                lines.append(f"a=extmap:{e['value']} {e['uri']}")
            if "rtcpMux" in m:
                lines.append("a=rtcp-mux")
            if "rtcpRsize" in m:
                lines.append("a=rtcp-rsize")
            for s in m.get("ssrcs", []):
                lines.append(f"a=ssrc:{s['id']} {s['attribute']}:{s['value']}")
            for c in m.get("candidates", []):
                val = f"{c['foundation']} {c['component']} {c['protocol']} {c['priority']} {c['ip']} {c['port']} typ {c['type']}"
                lines.append(f"a=candidate:{val}")
        return "\r\n".join(lines) + "\r\n"


def parse_offer_to_ortc(offer_sdp: str) -> dict[str, Any]:
    """Parse offer SDP to ORTC object, matching Agora SDK getOrtc logic."""
    parsed = SDPParser.parse(offer_sdp)
    ice_params = {}
    dtls_params = {}

    # Helper: Check if codec can be sent
    def can_send(codec_obj: dict) -> bool:
        name = codec_obj["rtpMap"]["encodingName"].upper()
        params = codec_obj.get("fmtp", {}).get("parameters", {})

        if name == "H265":
            return False
        if name == "VP9":
            # Profile 0 and 2 are sendable, 1 and 3 are not
            pid = params.get("profile-id")
            if pid in ("1", "3"):
                return False
        if name == "AV1":
            # Profile 1 is recv only
            if params.get("profile") == "1":
                return False
        return True

    # Global/Session level parameters
    if "iceUfrag" in parsed:
        ice_params = {"iceUfrag": parsed["iceUfrag"], "icePwd": parsed["icePwd"]}
    if parsed.get("fingerprints"):
        dtls_params = {
            "fingerprints": [
                {"hashFunction": fp["hash"], "fingerprint": fp["fingerprint"]}
                for fp in parsed["fingerprints"]
            ]
        }

    # Iterate media sections to extract params and build caps
    caps = {
        "send": {
            "audioCodecs": [],
            "audioExtensions": [],
            "videoCodecs": [],
            "videoExtensions": [],
        },
        "recv": {
            "audioCodecs": [],
            "audioExtensions": [],
            "videoCodecs": [],
            "videoExtensions": [],
        },
        "sendrecv": {
            "audioCodecs": [],
            "audioExtensions": [],
            "videoCodecs": [],
            "videoExtensions": [],
        },
    }

    for m in parsed.get("media", []):
        # Extract ICE/DTLS from first media section if not found globally
        if not ice_params and "iceUfrag" in m:
            ice_params = {"iceUfrag": m["iceUfrag"], "icePwd": m["icePwd"]}
        if not dtls_params and m.get("fingerprints"):
            dtls_params = {
                "fingerprints": [
                    {"hashFunction": fp["hash"], "fingerprint": fp["fingerprint"]}
                    for fp in m["fingerprints"]
                ]
            }

        mtype = m.get("type")
        codecs = []

        # Parse codecs
        for rtp in m.get("rtp", []):
            pt = rtp.get("payload")
            codec = {
                "payloadType": pt,
                "rtpMap": {
                    "encodingName": rtp.get("codec"),
                    "clockRate": rtp.get("rate"),
                },
                "rtcpFeedbacks": [],
                "fmtp": {"parameters": {}},
            }
            if rtp.get("encoding"):
                codec["rtpMap"]["encodingParameters"] = int(rtp.get("encoding"))

            # Feedbacks
            for fb in m.get("rtcpFb", []):
                if fb.get("payload") == pt:
                    fb_obj = {"type": fb.get("type")}
                    if fb.get("subtype"):
                        fb_obj["parameter"] = fb.get("subtype")
                    codec["rtcpFeedbacks"].append(fb_obj)

            # Add forced rrtr if missing
            if not any(fb["type"] == "rrtr" for fb in codec["rtcpFeedbacks"]):
                codec["rtcpFeedbacks"].append({"type": "rrtr"})

            # FMTP
            for f in m.get("fmtp", []):
                if f.get("payload") == pt:
                    for part in f.get("config", "").split(";"):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            codec["fmtp"]["parameters"][k.strip()] = v.strip()
                        elif part.strip():
                            # Handle flags or key-only params if any (less common in fmtp but possible)
                            # JS logic: params[k.trim()] = v ? v.trim() : null;
                            codec["fmtp"]["parameters"][part.strip()] = None
            codecs.append(codec)

        # Parse extensions
        extensions = [
            {"entry": ext.get("value"), "extensionName": ext.get("uri")}
            for ext in m.get("ext", [])
        ]

        # Distribute codecs based on can_send logic
        for codec in codecs:
            is_send = can_send(codec)
            is_recv = True  # Assumption: browser can recv what it offers

            target_lists = []
            if is_send and is_recv:
                target_lists.append(caps["sendrecv"])
            elif is_recv:
                target_lists.append(caps["recv"])

            for t in target_lists:
                if mtype == "audio":
                    t["audioCodecs"].append(codec)
                elif mtype == "video":
                    t["videoCodecs"].append(codec)

        # Extensions usually go to sendrecv
        target_ext = caps["sendrecv"]
        if mtype == "audio":
            target_ext["audioExtensions"].extend(extensions)
        elif mtype == "video":
            target_ext["videoExtensions"].extend(extensions)

    return {
        "iceParameters": ice_params,
        "dtlsParameters": dtls_params,
        "rtpCapabilities": caps,
        "version": "2",
    }


def generate_answer_from_ortc(
    ortc_params: dict[str, Any],
    offer_sdp: dict[str, Any],
    force_setup: str | None = None,
) -> str:
    """Generate answer SDP from ORTC parameters (from join_success)."""
    # ortc_params is from join_success (Agora)
    # offer_sdp is the dict returned by parse_offer_to_ortc(offer_sdp)
    dtls = ortc_params.get("dtlsParameters", {})
    ice = ortc_params.get("iceParameters", {})
    rtp_caps = ortc_params.get("rtpCapabilities", {})
    cname = ortc_params.get("cname", "")
    offer_parsed = offer_sdp

    # setup logic from yx(): server -> passive, client -> active, auto -> actpass
    role = dtls.get("role", "server")
    setup = force_setup or (
        "passive" if role == "server" else "active" if role == "client" else "actpass"
    )

    answer = {
        "version": 0,
        "origin": {
            "username": "-",
            "sessionId": 0,
            "sessionVersion": 0,
            "netType": "IN",
            "ipVer": 4,
            "address": "127.0.0.1",
        },
        "name": "AgoraGateway",
        "groups": offer_parsed.get("groups", [{"type": "BUNDLE", "mids": "0 1"}]),
        "msidSemantic": offer_parsed.get(
            "msidSemantic", {"semantic": "WMS", "token": ""}
        ),
        "icelite": "ice-lite",
        "media": [],
        "extmapAllowMixed": "extmap-allow-mixed",
    }

    # Match IDs from original offer
    offer_ext_map = {
        ext.get("extensionName"): ext.get("entry")
        for m in offer_parsed.get("media", [])
        for ext in m.get("ext", [])
    }

    for idx, offer_m in enumerate(offer_parsed.get("media", [])):
        mtype = offer_m.get("type", "audio")
        mid = offer_m.get("mid", str(idx))

        # RTP capabilities can be flat or nested
        caps = rtp_caps
        if "recv" in rtp_caps:
            caps = rtp_caps["recv"]
        elif "sendrecv" in rtp_caps:
            caps = rtp_caps["sendrecv"]

        codecs = caps.get("videoCodecs" if mtype == "video" else "audioCodecs", [])
        extensions = caps.get(
            "videoExtensions" if mtype == "video" else "audioExtensions", []
        )

        answer_m = {
            "type": mtype,
            "port": 9,
            "protocol": "UDP/TLS/RTP/SAVPF",
            "payloads": " ".join([str(c.get("payloadType")) for c in codecs]),
            "connection": {"version": 4, "ip": "0.0.0.0"},
            "rtcp": {"port": 9, "netType": "IN", "ipVer": 4, "address": "0.0.0.0"},
            "iceUfrag": ice.get("iceUfrag"),
            "icePwd": ice.get("icePwd"),
            "iceOptions": "trickle",
            "fingerprints": [],
            "setup": setup,
            "mid": mid,
            "direction": "sendonly",
            "rtp": [],
            "rtcpFb": [],
            "fmtp": [],
            "ext": [],
            "rtcpMux": "rtcp-mux",
            "rtcpRsize": "rtcp-rsize",
        }

        # Handle multiple fingerprints
        dtls_fps = dtls.get("fingerprints", [])
        if not dtls_fps:
            # Fallback if single
            dtls_fps = [
                {"hashFunction": "sha-256", "fingerprint": ""}
            ]  # Should usually be present

        for fp in dtls_fps:
            answer_m["fingerprints"].append(
                {
                    "hash": fp.get("hashFunction", fp.get("algorithm", "sha-256")),
                    "fingerprint": fp.get("fingerprint"),
                }
            )

        for c in codecs:
            pt = c.get("payloadType")
            answer_m["rtp"].append(
                {
                    "payload": pt,
                    "codec": c["rtpMap"].get("encodingName"),
                    "rate": c["rtpMap"].get("clockRate"),
                    "encoding": c["rtpMap"].get("encodingParameters"),
                }
            )
            for fb in c.get("rtcpFeedbacks", []):
                answer_m["rtcpFb"].append(
                    {
                        "payload": pt,
                        "type": fb.get("type"),
                        "subtype": fb.get("parameter"),
                    }
                )

            # handle fmtp
            fmtp_params = c.get("fmtp", {}).get("parameters", {})

            # Match JS: Force stereo for opus
            if c["rtpMap"].get("encodingName", "").lower() == "opus":
                fmtp_params["stereo"] = "1"
                fmtp_params["sprop-stereo"] = "1"

            if fmtp_params:
                config_parts = []
                for k, v in fmtp_params.items():
                    if v is not None:
                        config_parts.append(f"{k}={v}")
                    else:
                        config_parts.append(k)

                answer_m["fmtp"].append(
                    {
                        "payload": pt,
                        "config": ";".join(config_parts),
                    }
                )

        for ext in extensions:
            uri = ext.get("extensionName")
            if uri in offer_ext_map:
                answer_m["ext"].append({"value": offer_ext_map[uri], "uri": uri})

        # Note: Working SDK for audience role does NOT include SSRC lines in the answer
        # if cname: answer_m["ssrcs"] = [{"id": 12345678 if mtype == "audio" else 87654321, "attribute": "cname", "value": cname}]

        # Add Candidates from iceParameters.candidates if any
        if "candidates" in ice:
            answer_m["candidates"] = []
            for c in ice["candidates"]:
                answer_m["candidates"].append(
                    {
                        "foundation": c.get("foundation", "0"),
                        "component": c.get("component", 1),
                        "protocol": c.get("protocol", "udp"),
                        "priority": c.get("priority", 0),
                        "ip": c.get("ip"),
                        "port": c.get("port"),
                        "type": c.get("type"),
                    }
                )

        answer["media"].append(answer_m)

    return SDPParser.write(answer)
