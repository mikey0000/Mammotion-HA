# Repairs platform
<!-- Ported from home-assistant/core .claude/skills/ha-integration-knowledge/platform-repairs.md (Apache-2.0, (c) Home Assistant contributors); paths adapted to custom_components/mammotion -->

Platform exists as `custom_components/mammotion/repairs.py`.

- **Actionable Issues Required**: All repair issues must be actionable for end users
- **Issue Content Requirements**:
  - Clearly explain what is happening
  - Provide specific steps users need to take to resolve the issue
  - Use friendly, helpful language
  - Include relevant context (device names, error details, etc.)
- **String Content Must Include**:
  - What the problem is
  - Why it matters
  - Exact steps to resolve (numbered list when multiple steps)
  - What to expect after following the steps
- **Avoid Vague Instructions**: Don't just say "update firmware" - provide specific steps
- **Severity Guidelines**:
  - `CRITICAL`: Reserved for extreme scenarios only
  - `ERROR`: Requires immediate user attention
  - `WARNING`: Indicates future potential breakage
- Only create issues for problems users can potentially resolve

## In this repository

- Repair issues are created in `__init__.py` (`cloud_login_failed` / `cloud_login_retrying`, `account_in_use_<account>`). Each one must be deleted on recovery *and* on unload; an issue that outlives its entry is a bug.
- Translation keys live under `issues` in `strings.json` and every `translations/*.json`.
