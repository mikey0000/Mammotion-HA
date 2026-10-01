---
name: ha-integration-knowledge
description: Everything you need to know to build, test and review Home Assistant Integrations. If you're looking at an integration, you must use this as your primary reference.
---
<!-- Ported from home-assistant/core .claude/skills/ha-integration-knowledge/SKILL.md (Apache-2.0, (c) Home Assistant contributors); paths adapted to this custom integration, "In this repository" is local. -->

## File Locations
- **Integration code**: `./custom_components/mammotion/`
- **Integration tests**: `./tests_ha/` (see `docs/testing.md`)
- **Library**: `pymammotion` (the Luba-API repository). Protocol, transport, auth and device-state logic live there.

## General guidelines

- When looking for examples, prefer integrations with the platinum or gold quality scale level first.
- Polling intervals are NOT user-configurable. Never add scan_interval, update_interval, or polling frequency options to config flows or config entries.
- Do NOT allow users to set config entry names in config flows. Names are automatically generated or can be customized later in UI. Exception: helper integrations may allow custom names.
- For entity actions and entity services, avoid requesting redundant defensive checks for fields already enforced by Home Assistant validation schemas and entity filters; only request extra guards when values bypass validation or are transformed unsafely.
- When validation guarantees a key is present, prefer direct dictionary indexing (`data["key"]`) over `.get("key")` so invalid assumptions fail fast.
- Integrations should be thin wrappers. Protocol parsing, device state machines, or other domain logic belong in a separate PyPI library, not in the integration itself. If unsure, ask before inlining.
- Integrations should not implement fixes or workarounds for limitations in libraries. Instead, the library should be updated to fix the issue.

The following platforms have extra guidelines:
- **Diagnostics**: [`platform-diagnostics.md`](platform-diagnostics.md) for diagnostic data collection
- **Repairs**: [`platform-repairs.md`](platform-repairs.md) for user-actionable repair issues

## Entity platforms

- Ensure `async_added_to_hass()` and `async_will_remove_from_hass()` have symmetrical behavior. For example, if a subscription is created in `async_added_to_hass()`, it should be unsubscribed in `async_will_remove_from_hass()`. Also, if something is torn down in `async_will_remove_from_hass()`, it should be set up in `async_added_to_hass()`.
- Entity base class (e.g. `SensorEntity`, `TrackerEntity`) provide a stable API for child classes to inherit from. Do not suggest redeclaring or duplicating attributes, properties, or methods the base class already provides, and do not add guards against the parent's behavior changing — rely on the base class instead.

## Integration Quality Scale

- When validating the quality scale rules, check them at https://developers.home-assistant.io/docs/core/integration-quality-scale/rules
- When implementing or reviewing an integration, always consider the quality scale rules, since they promote best practices.

This is a custom integration, so there is no `quality_scale.yaml` and hassfest does not grade it. The rule-by-rule status lives in `docs/quality_scale.md` (done / todo / exempt, with file references). Keep it current when a change implements or regresses a rule.

### How Rules Apply
1. **Bronze Rules**: Always apply.
2. **Higher Tier Rules**: Apply as targets; `docs/quality_scale.md` ranks the open ones.
3. **Rule Status**: `docs/quality_scale.md` records each rule as:
   - `done`: Rule implemented
   - `exempt`: Rule doesn't apply (with reason)
   - `todo`: Rule needs implementation

## Testing Requirements

- Tests should avoid interacting or mocking internal integration details. For more info, see https://developers.home-assistant.io/docs/development_testing/#writing-tests-for-integrations
- The local rules are `docs/testing.md`; every touched `tests_ha/` file is reviewed by the `test-reviewer` agent.

## In this repository

The defects these rules most often catch here, each with the rule it breaks:

- **Service schema defaults overwrite held values** (`action-setup`, #913). `vol.Optional(..., default=...)` is filled in on every call, so an edit/start/modify handler cannot tell "omitted" from "asked for the default" and overwrites what an entity or stored plan holds. Omitted means "leave it"; `services.yaml` must not advertise a default the schema does not apply.
- **State read once, acted on after an await** (#918). A value read from `coordinator.data` before `await` may be stale after it; re-read after the await, or wait on the coordinator's listeners with a bounded timeout.
- **A user action that fails must raise** (`action-exceptions`, `exception-translations`, #914). No log-and-return, no `pass`, no swallowing default. Map library errors to `HomeAssistantError` (or `ServiceValidationError` for bad input) with `translation_domain`/`translation_key`; use the shared `COMMAND_EXCEPTIONS` tuple from `const.py` rather than a copied list.
- **A catch-all must not turn a transient error into a value** (`test-before-setup`, #915). Transient → `ConfigEntryNotReady`; rejected credentials → `ConfigEntryAuthFailed`; anything else → `ConfigEntryError`. Never `except Exception: return False`.
- **Except clauses ordered child before parent.** A subclass caught after its parent is dead code (the `AccountInUseError` branch behind its parent class).
- **Firmware fields are untrusted** (#916). Validate raw values (zero timestamps, uptime-relative times, out-of-range enums) before turning them into HA state.
- **Tables come from pymammotion** (#917). Mode tables such as `NO_REQUEST_MODES` and refresh triggers are imported, not copied and hand-picked.
- **Diagnostics redaction is by key name** (`diagnostics`, #919). See `platform-diagnostics.md`.
- **Unload must not persist stale credentials.** A flow or another entry may have saved newer ones; unload writes only what this entry still owns.
- **Config-flow error mapping is one helper**, shared by the user, reauth and reconfigure steps, so the steps cannot drift.
