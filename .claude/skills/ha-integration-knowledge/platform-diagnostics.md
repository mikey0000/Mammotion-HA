# Integration Diagnostics
<!-- Ported from home-assistant/core .claude/skills/ha-integration-knowledge/platform-diagnostics.md (Apache-2.0, (c) Home Assistant contributors); paths adapted to custom_components/mammotion -->

Platform exists as `custom_components/mammotion/diagnostics.py`.

- **Required**: Implement diagnostic data collection
- **Security**: Never expose passwords, tokens, or sensitive coordinates

## In this repository

- `TO_REDACT` in `diagnostics.py` is matched by key name at any depth, so it must name every identifier field the pymammotion models carry, not only the ones someone noticed in a pasted dump. The cellular block's `imei`/`imsi`/`iccid` were exported verbatim until #919.
- When pymammotion adds a model field, check its name against the identifier patterns (`imei`, `imsi`, `iccid`, `mac`, `ssid`, `ip`, `token`, `key`, `secret`, `password`, `serial`). `docs/testing.md` "Mechanical conventions" specifies the test that asserts this.
- `iot_id`, `identity_id`, `nick_name` and `lat`/`lon` are deliberately kept (multi-device dumps and coordinate bugs need them); the reason is in the comment above `TO_REDACT`. Do not "fix" that.
