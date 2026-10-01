# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build Commands

- Install dependencies: `uv sync`
- Run in environment: `uv run`
- Run tests: `uv run --no-sync python -m pytest tests_ha -q`
- Coverage: add `--cov --cov-report=term-missing` (config in `pyproject.toml`; CI enforces a floor)
- Type checking: `uv run mypy custom_components/`
- Format code: `uv run ruff format`
- Lint code: `uv run ruff check`
- Run pre-commit: `uv run pre-commit run --all-files`

## Code Style Guidelines

- Python 3.14 target with strong typing (mypy)
- Follow Home Assistant integration patterns
- Use async/await patterns (prefix functions with `async_`)
- Class methods use `cls`, instance methods use `self`
- Variables in class scope should not be mixedCase
- Imports organized with isort (via ruff)
- Catch specific exceptions, use `raise ... from exc` pattern
- Docstrings required (enforced by ruff D rules)
- Line ending format: LF
- Prefer specific exception types over broad ones
- Type annotations required (autotyping hook)

When making changes, follow existing patterns in similar files and follow Home Assistant best practices.

### Python syntax notes

- Python 3.14 is the minimum. Do not flag 3.14-only syntax or suggest workarounds for older versions.
- `except TypeA, TypeB:` without parentheses is valid (PEP 758). Never flag it.
- Annotations are evaluated lazily (PEP 649): unquoted forward references are fine.

## Agent toolkit

Ported from Home Assistant core's `.claude/` (Apache-2.0) and adapted:

- `ha-integration-knowledge` skill — the primary reference for any integration change; its "In this repository" list names the defect families that keep recurring here (schema defaults over held values, state read before an await, silent action failures, catch-alls returning values, shadowed `except` clauses, unvalidated firmware fields, copied library tables, incomplete redaction).
- `ha-review` (local diff) and `ha-pr-reviewer` + `ha-pr-comment-audit` (GitHub PRs, console only) for reviews.
- `ha-quality-scale-verify` for a rule; the rule-by-rule status is `docs/quality_scale.md`.
- `test-reviewer` agent for tests (see Testing below).

Integrations with Gold or Platinum on the quality scale in Home Assistant core are good places to look for examples.

## Home Assistant Integration Rules

- All imports within the integration must be relative (e.g. `from . import Foo`, `from .services import bar`). Never use `from custom_components.mammotion import ...` — HA loads integrations in a way that makes absolute imports from `custom_components` fail at runtime.

## Bluetooth via ESPHome proxies

- Most users reach the mower over BLE through ESP32 (ESPHome) proxies, not a local adapter. Home Assistant's bluetooth integration owns scanning; this integration only forwards advertisements to pymammotion: `_register_ble_reconnect_callback` in `__init__.py` (per configured mower) and the report coordinator's `_add_ble_device` / `update_ble_device` path. Both re-create the BLE transport if it is missing, so any place that must _keep_ it detached (the Bluetooth switch) has to guard them on the stored switch state.
- Connect, cooldown, RSSI gating and the stale GATT-cache recovery live in pymammotion's `BLETransport` (see the BLE notes in Luba-API's CLAUDE.md). Do not add reconnect or retry logic here; fix it in the library.
- The emergency nudge buttons are available only while the transport is usable (fresh advertisement, RSSI above `-90`, not in cooldown) or Wi-Fi movement is enabled. Flapping availability at the edge of proxy range is expected; a button's state changing back to its last-pressed timestamp when it becomes available again is not a press.
- Symptom map from reports: "Characteristic 0000ff02 … was not found" right after connect is a stale cached GATT table (recovered in pymammotion ≥ 0.9.0b3); "in cooldown (120s remaining)" follows one real failure; RSSI 0 in the report frame plus unavailable nudge buttons means the link is down, not that the proxy stopped hearing the mower.

## Camera streaming (Agora)

- Agora signalling comes from the `pyagorartc` library (source and docs in the PyAgoraRTC repo; `docs/migration.md` §2 is the Mammotion mapping). Do not add SDP, gateway or access-point code here; fix it in the library.
- `stream_session.py` holds the pure conversions (stream token → `ChannelCredentials`, AP answer → `RTCIceServer`, browser candidate → `IceCandidate`) and the AP call; `coordinator.async_check_stream_expiry` caches the token and AP answer.
- `camera.py` builds one `AgoraSession` per offer (single use) and supplies the host callbacks: `_fpv_keepalive` (MQTT `refresh_fpv` on 4G, `False` on WiFi), `_on_peer_left` (BLE sync + re-subscribe), `_on_closed` (503 to viewers, release the feed). The `availableTime` deadline is set only on 4G.
- Only candidates that arrive before the join reach Agora; the gateway has no trickle message.

## Translations

- When adding or renaming any entity (sensor, switch, button, number, select, etc.) or an ENUM entity state, you MUST update the translations in **every** language file, not just English.
- The files to keep in sync: `custom_components/mammotion/strings.json` (the source) **and** every file under `custom_components/mammotion/translations/` (`en`, `cs`, `da`, `de`, `fr`, `hu`, `it`, `nl`, `pl`, `ro`, `sl`, `sv`, plus any new locale present in that directory). Treat the directory listing as the source of truth for which languages exist rather than this hard-coded list.
- Translate the entity `name` and every ENUM `state` value into each language's own language — do not copy the English text into the other locales as a placeholder.
- Also add an icon entry in `custom_components/mammotion/icons.json` for the new entity where appropriate.
- After editing, confirm every JSON file still parses and that the new key (with all its `state` values) is present in each file before considering the change complete.
- `translations/en.json` is an exact copy of `strings.json` (there is no `script.translations` here), and tests load `en.json`, not `strings.json`. Copy `strings.json` over `translations/en.json` after editing it, before running tests.

## Testing (rules for Claude)

`docs/testing.md` is the testing constitution for `tests_ha/`. Read it before writing or editing a test; the rules below are the ones most often broken.

- Drive the real setup and unload (`MockConfigEntry` + `hass.config_entries.async_setup` / `async_unload`) and assert on entity states, registries, `entry.state` and raised errors. Do not patch `async_setup_entry` except through the one config-flow `mock_setup_entry` pattern.
- Spec every mock (`create_autospec(MammotionClient, instance=True)`); never a bare `MagicMock()`, never mock the unit under test.
- No real sleeps: `freezer.tick` + `async_fire_time_changed(hass)` + `await hass.async_block_till_done()` for timers; bound every wait.
- Type every test parameter, prefer `@pytest.mark.usefixtures` for unused fixtures, no branching in a test, `pytest.param(..., id=...)` for near-duplicates, syrupy snapshots for large stable output.
- A user action's failure path is tested: it raises a translated `HomeAssistantError`.
- A regression test is seen red before the fix, marked `@pytest.mark.regression`, named for the behaviour, with a docstring recording the defect.
- Name test modules for the source module (`test_<module>[_<concern>].py`); shared builders go in a `*_support.py`.
- **Every touched `tests_ha/` file is reviewed by the `test-reviewer` agent** before the work is reported complete. A `PostToolUse` hook queues the files and the `Stop` hook refuses the first stop while the queue is non-empty (`.claude/settings.json`, `.claude/hooks/test_review_gate.py`). The author fixes; the reviewer does not rewrite.
- CI enforces a coverage floor (`.github/workflows/tests.yml`); raise it when coverage rises, never lower it.

## Good practices

- When reviewing entity actions, do not suggest extra defensive checks for input fields that are already validated by Home Assistant's service/action schemas and entity selection filters. Suggest additional guards only when data bypasses those validators or is transformed into a less-safe form.
- When validation guarantees a dict key exists, prefer direct key access (`data["key"]`) instead of `.get("key")` so contract violations are surfaced instead of silently masked.
- Keep comments concise. Prefer one short line stating the non-obvious constraint, or no comment at all.
- Do not add comments that just restate the code on the following line(s) (e.g. `# Check if initialized` above `if self.initialized:`). Comments should only explain why (non-obvious constraints, surprising behavior, or workarounds), never what. Never add comments that justify a change by referencing what the code looked like before. Comments in tests that explain why a function call or assertion is made are ok.
- Do not add section or divider comments (e.g. `# --- XYZ Triggers ---`) inside or outside of functions, since those can easily become stale and be misleading.
- When catching exceptions, try-clauses should be as small as possible, i.e. avoid wrapping large blocks of code in a try-clause, and avoid catching exceptions from functions that are not expected to raise them.
- Sensitive service actions, i.e. those that can change configuration or have security implications, should require an admin user. Register them with the `async_register_admin_service` service helper, which checks this for you.
