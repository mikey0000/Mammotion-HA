# Integration Quality Scale: Mammotion

This custom integration has no `quality_scale.yaml` and hassfest does not
grade it, so the status lives here. Rule ids and tiers are hassfest's
(`script/hassfest/quality_scale.py` in home-assistant/core); each rule's text
is at
`https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/<rule>`.
Verify a rule with the `ha-quality-scale-verify` skill and update its row.

Assessed 2026-10-02 against branch `fixes-913-919` with other edits in flight,
so line numbers are deliberately omitted where the file was changing; refer
by function. Coverage figures are from one run on that tree (70% total).

Status: **done** / **partial** (implemented with known gaps) / **todo** /
**exempt** (with reason).

## Bronze

| Rule | Status | Evidence / gap |
|---|---|---|
| action-setup | **partial** | `services.async_setup_services` is registered from `async_setup` (good). `camera.async_setup_platform_services` registers `refresh_stream`, `start_video`, `stop_video`, `get_tokens`, `move_*` from the camera platform's `async_setup_entry`, bound to *that* entry's mowers: with two accounts the last-loaded entry's handlers replace the first's, and the services vanish while no entry is loaded instead of raising. Move them to `async_setup` and resolve the target entry at call time. |
| appropriate-polling | done | Push-first (`iot_class: local_push`); per-mode poll cadence lives in pymammotion. |
| brands | todo | No brand assets for this domain; custom integrations can ship `brand/icon.png` etc. in the integration directory (`placeholder.png` is a camera placeholder, not a brand icon). |
| common-modules | done | `coordinator.py`, `entity.py`. |
| config-flow | done | `config_flow.py`, `manifest.json` `config_flow: true`. |
| config-flow-test-coverage | **todo** | `config_flow.py` measured at 49% line / 57 partial branches. Every `errors["base"]` value and recovery from it must be reached; the options flow `async_step_init` measured 0%. |
| dependency-transparency | done | `pymammotion`, `pyagorartc` on PyPI with tagged CI releases. |
| docs-actions | partial | `services.yaml` + translations describe every action; the README/wiki has no per-action page. |
| docs-conditions | exempt | No conditions. |
| docs-high-level-description | done | `README.md`. |
| docs-installation-instructions | done | `README.md` "Installation". |
| docs-removal-instructions | todo | `README.md` has no removal section. |
| docs-triggers | exempt | No device triggers. |
| entity-event-setup | done | Subscriptions in `async_added_to_hass` via `async_on_remove` (`entity.py`). |
| entity-unique-id | done | `f"{coordinator.unique_name}_{key}"` (`entity.py`). |
| has-entity-name | done | `_attr_has_entity_name = True` on the base entities. |
| runtime-data | done | `entry.runtime_data` (`MammotionDevices`). |
| test-before-configure | done | User, reauth and reconfigure steps log in before creating/updating. |
| test-before-setup | **partial** | Since d2666cd setup raises `ConfigEntryNotReady` for transient login errors and `ConfigEntryError`/`ConfigEntryAuthFailed` otherwise when no BLE mower exists (#915). With a BLE mower it continues BLE-only by design. Gap: no mechanical guard against a new catch-all that returns a value (`docs/testing.md` §14.4). |
| unique-config-entry | done | `async_set_unique_id` (account or MAC) + `_abort_if_unique_id_configured` in each creating step. |

## Silver

| Rule | Status | Evidence / gap |
|---|---|---|
| action-exceptions | **partial** | Entity actions and coordinator direct sends map `COMMAND_EXCEPTIONS` to translated `HomeAssistantError` since d2666cd (#914). Gaps: `camera.py` movement/stream service handlers do nothing when the entity resolves to no mower (`if mower:`), and log-and-default on bad speed input instead of `ServiceValidationError`; not asserted mechanically across all action methods (§14.5). |
| config-entry-unloading | done | `async_unload_entry` (unloads platforms, stops handles, flushes the store; no credential write-back). `test_unload_credentials.py`. |
| docs-configuration-parameters | partial | Options flow has `prefer_ble`, notifications etc.; not documented in README. |
| docs-installation-parameters | partial | Account/password and BLE path explained in README, not per field. |
| entity-unavailable | done | `available` on the base entities keys off `coordinator.is_online()` and data presence (`entity.py`). |
| integration-owner | done | `codeowners: ["@mikey0000"]`. |
| log-when-unavailable | **todo (verify)** | The coordinators do not raise `UpdateFailed` (none in `coordinator.py`), so HA's once-only "unavailable / back online" logging never runs; whether the integration logs the transition once itself is unverified. |
| parallel-updates | **todo** | No platform sets `PARALLEL_UPDATES`. Add `PARALLEL_UPDATES = 0` to read-only platforms (sensor, binary_sensor, device_tracker, event) and `1` to action platforms (button, switch, number, select, lawn_mower, vacuum, update, camera) — the mower serialises commands anyway, and concurrent user commands race the queue. Mechanical: a meta-test that every platform module defines it. |
| reauthentication-flow | done | `async_step_reauth` / `async_step_reauth_confirm`, `_abort_if_unique_id_mismatch(reason="wrong_account")`. |
| test-coverage | **todo** | 70% total (branch coverage on, tree mid-edit); Silver wants >95%. Floor enforced in CI (`.github/workflows/tests.yml`). `update.py` and `device_tracker.py` 0%. |

## Gold

| Rule | Status | Evidence / gap |
|---|---|---|
| devices | done | `device_info` on base entities; device registry updates in `entity.py`. |
| diagnostics | **partial** | `diagnostics.py` with `TO_REDACT`; #919 showed the list is curated by hand. Gap: no check that it covers every identifier field in the pymammotion models (§14.7), no snapshot of a full dump. |
| discovery | done | `bluetooth` matchers in `manifest.json`, `async_step_bluetooth`. |
| discovery-update-info | exempt | BLE address is the unique id and does not change; no network host to update. |
| docs-data-update | todo | |
| docs-examples | partial | README dashboard plugins; no automation examples. |
| docs-known-limitations | todo | |
| docs-supported-devices | partial | README lists families; no model table. |
| docs-supported-functions | partial | |
| docs-troubleshooting | done | README "Troubleshooting". |
| docs-use-cases | todo | |
| dynamic-devices | partial | `check_for_new_devices=True` at login; a device added to the account after setup needs a reload. |
| entity-category | done | `EntityCategory` on config/diagnostic entities. |
| entity-device-class | done | `device_class` set where one exists. |
| entity-disabled-by-default | partial | Two entities use `entity_registry_enabled_default`; the many diagnostic sensors are enabled. |
| entity-translations | done | Descriptions carry `translation_key`; `strings.json` + 12 locales. Mechanical parity check exists (`test_translations_not_english.py`). |
| exception-translations | **partial** | Most raises carry `translation_domain`/`translation_key`; at least one f-string raise remains (coordinator credential refresh failure). §14.5 makes it a ratchet. |
| icon-translations | done | `icons.json`. |
| reconfiguration-flow | done | `async_step_reconfigure` → `async_update_reload_and_abort`. Gap: the login→`errors["base"]` mapping is repeated in the user, wifi, reauth, reconfigure and options steps; one helper would keep them from drifting. |
| repair-issues | done | `cloud_login_failed`/`cloud_login_retrying`, `account_in_use_<account>` (`__init__.py`), deleted on recovery/unload. |
| stale-devices | done | `async_remove_config_entry_device`, `on_device_removed` callback. |

## Platinum

| Rule | Status | Evidence / gap |
|---|---|---|
| async-dependency | done | pymammotion and pyagorartc are asyncio. |
| inject-websession | partial | HA's session is passed for login/restore and the Agora AP client. pymammotion still opens its own `ClientSession` in `aliyun/cloud_gateway.py` (two calls), `aliyun/tea/core.py` and one path in `http/http.py` — fix in the library. |
| strict-typing | **todo** | The pre-commit `mypy --strict` hook fails on ~67 pre-existing errors, so commits bypass it. |

## Ranked todo list: the rules that would have caught today's bugs

1. **action-exceptions + exception-translations** — #914 (log-and-return,
   `pass`, swallowing defaults, raw exceptions escaping), the dead
   `except` branch, and the copied exception lists. Make it mechanical:
   §14.3 (clause order), §14.5 (actions raise only translated
   `HomeAssistantError`), §14.6 (no copied tuples). Fix `camera.py`'s silent
   service handlers.
2. **action-setup** — #913 is an action-schema defect (defaults overwriting
   held values); §14.1/§14.2 already pin it (`tests_ha/meta/test_conventions_schemas.py`).
   Also move the camera services to `async_setup`.
3. **test-before-setup** — #915 (catch-all returning `False`). §14.4 forbids
   the shape; tests assert `entry.state` per failure class (`docs/testing.md`
   §11).
4. **diagnostics** — #919. §14.7 walks the pymammotion models for
   identifier-shaped field names against `TO_REDACT`; add a snapshot of a full
   dump.
5. **config-flow-test-coverage** (and the reconfiguration-flow gap) — the
   duplicated error mapping across steps and the 49% coverage. One mapping
   helper, and a parametrized test over every step × error.
6. **test-coverage** — the CI floor (69%) catches a change that adds untested
   branches; #918 (state read before an await) and the unload-credentials bug
   are timing/lifecycle defects only a real setup → act → unload test finds
   (`docs/testing.md` §5, §6).
7. **parallel-updates** — cheap, mechanical (every platform defines
   `PARALLEL_UPDATES`), and it bounds concurrent user commands racing each
   other, the same shape as #918.
8. **log-when-unavailable / entity-unavailable** — verify the transition is
   logged once; relevant to #916/#917, where refresh triggers were hand-picked
   and a device sat stale without anyone noticing. §14.8 pins the tables to
   pymammotion.
9. **strict-typing** — would have flagged several `None`-unsafe reads of raw
   firmware fields (#916); start with `--strict` on `diagnostics.py`,
   `services.py` and `config_flow.py` and ratchet.
10. **reauthentication-flow / unique-config-entry** — done; keep them pinned
    by the existing flow tests when the error mapping is consolidated.

## Mechanically testable rules

| Rule | How |
|---|---|
| parallel-updates | AST: every platform module in `PLATFORMS` assigns module-level `PARALLEL_UPDATES`. |
| action-setup | Every `hass.services.async_register` call sits in a function reachable from `async_setup`, never from a platform `async_setup_entry` (AST over call sites). |
| action-exceptions | §14.3, §14.5, §14.6. |
| exception-translations | §14.5: every `raise HomeAssistantError/ServiceValidationError(...)` has `translation_key`, and §14.9: the key exists under `exceptions`. |
| diagnostics | §14.7 plus a syrupy snapshot of a populated dump. |
| entity-translations / icon-translations | §14.9. |
| unique-config-entry | Flow test: a second identical user flow aborts `already_configured`. |
| test-before-setup | §14.4 plus `entry.state` per failure class. |
| test-coverage | `--cov-fail-under` in CI. |
| config-flow-test-coverage | `coverage report --include=custom_components/mammotion/config_flow.py --fail-under=100` as a separate CI step once it is reached. |
| strict-typing | `mypy --strict` on an allow-list of modules that only grows. |
