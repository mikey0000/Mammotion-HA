# Testing constitution

The rules every test in `tests_ha/` follows, and that every agent writing a
test here obeys. Modelled on the Luba-API (pymammotion) constitution; the
section numbers match it where the rule is the same, so a finding cited as
"§6 time" means the same thing in both repositories.

The suite exists to make a regression impossible to merge quietly. A test that
passes whether or not the behaviour it names is intact is worse than no test:
it costs a run every commit and buys nothing, and it tells the next reader the
behaviour is covered. Most of the rules below are that one rule, applied.

Enforcement:

- `tests_ha/meta/` — the mechanical rules, asserted by the suite itself, with
  a frozen baseline of pre-existing offenders that may only shrink (§14;
  `pytest tests_ha/meta` runs them all).
- the `test-reviewer` agent (`.claude/agents/test-reviewer.md`) — the
  judgement-shaped rules, run against every test an agent writes (§13).
- coverage in CI (`.github/workflows/tests.yml`, §12) — a floor that only rises.

---

## 1. What a test here may touch

There is one tier: a real Home Assistant from
`pytest-homeassistant-custom-component` (the `hass` fixture), the real
integration, the real pymammotion models, and doubles only at the edge where
pymammotion would do I/O.

| May touch | Must not touch |
|---|---|
| `hass`, the config-entry, entity, device and issue registries, `MockConfigEntry`, real platforms | the network, a real Mammotion account, real Bluetooth hardware |
| real `MowingDevice` / `Device` / protobuf models, real `DeviceHandle` with a scripted transport | the real clock (§6), files outside `tmp_path`, `~` |
| a spec'd `MammotionClient` stand-in, hand-written fakes (`FakeAgoraSession`) | private HA internals beyond what `pytest_homeassistant_custom_component.common` exposes |

Budget: under 1 s per test; the whole suite under a minute. A test that needs
more is doing real waiting (§6).

Behaviour that lives in pymammotion is tested in Luba-API, not here. A test in
`tests_ha/` that only re-asserts what the library does is either testing the
wrong repository or is missing the integration's half (the entity state, the
raised error, the service response).

---

## 2. Layout and file names

```
tests_ha/
├── conftest.py               global autouse safety nets only
├── <feature>_support.py      shared builders and fakes (one per concern)
├── ble_advertisements.py     builders for HA's bluetooth manager
├── fixtures/                 static payloads (*.json, *.geojson)
├── meta/                     the mechanical conventions (§14)
└── test_<module>[_<concern>].py
```

- **Name a test module for the source module it exercises.** A test of
  `custom_components/mammotion/lawn_mower.py` is `test_lawn_mower.py` or, past
  ~600 lines or for a distinct seam, `test_lawn_mower_<concern>.py`
  (`test_lawn_mower_failure_keys.py`). `__init__.py` maps to `test_init_*`.
  The prefix is what makes "is this already covered?" answerable with one
  `ls tests_ha/test_<module>*`.
- **Feature-named and defect-named files are legacy.** 28 of 101 modules
  mirror a source module today; `test_edit_task_keeps_enabled.py` reads as a
  bug report, not a unit of the codebase. New tests go into the mirrored file;
  a defect's pin goes with the module it pins (§7). Do not rename the legacy
  files in bulk as a side effect of unrelated work (§15).
- **Shared code lives in a `*_support.py` module**, imported by name. The
  moment a second test module needs a builder, it moves there. Two modules
  each defining `_make_mower` is the failure this rule exists to stop.
  `conftest.py` holds only autouse safety nets (the poll-loop stopper, enabling
  custom integrations) — a conftest fixture is invisible at the call site.
- **No `test_*.py` outside `tests_ha/`.** The repo-root `test_sdp.py` and
  `test_credential_fallback.py` are scratch: delete them or move them in.
- **No test data inline past a few lines.** Captured frames and JSON payloads
  go in `tests_ha/fixtures/`, loaded through a helper.

---

## 3. Test names and docstrings

```python
async def test_<subject>_<expected behaviour>[_when_<condition>](hass: HomeAssistant) -> None:
```

`test_start_mow_keeps_the_working_speed_when_the_call_omits_it`, not
`test_start_mow_2`. The name is the failure report CI shows.

Every test module has a docstring saying what surface it covers. A test gets a
docstring only when the name cannot carry the why; a regression test always
has one (§7). Do not restate the name.

---

## 4. Fixtures and builders

- **`@pytest.fixture`** for anything with a lifecycle (a loaded config entry,
  a patch that must be undone, an injected advertisement). Scope it to
  `function` and `yield`.
- **A plain `make_<thing>()` builder** for anything that is just
  construction, with keyword overrides. Three fixtures differing by one field
  are one builder with a default.
- **Prefer `@pytest.mark.usefixtures("name")`** over taking a fixture argument
  the test never reads.
- Placement, in order: the test module → the concern's `*_support.py` →
  `conftest.py` (global, autouse only).

---

## 5. Test doubles

Prefer, in this order, and justify every step down:

1. **The real object.** `hass`, `MockConfigEntry`, real platforms and
   registries, real `MowingDevice`, real coordinators over a loaded entry, a
   real `DeviceHandle`. All are cheap.
2. **A hand-written fake** in a `*_support.py` (the `FakeAgoraSession`, the
   remote-drive rig's scripted token endpoint) for a protocol that would do
   I/O.
3. **`unittest.mock`** — at the pymammotion boundary, or to observe a callback.

Rules for the third:

- **Spec every mock.** The client is `create_autospec(MammotionClient,
  instance=True)` or `MagicMock(spec=MammotionClient)`; a coordinator stand-in
  is `MagicMock(spec=MammotionReportUpdateCoordinator)`. A bare `MagicMock()`
  answers every attribute truthily forever: rename the method under test and
  the assertion still passes. There are 183 bare `MagicMock()` calls in 51
  modules today (§15).
- **Never mock the unit under test.** Patching the coordinator method the
  entity calls and then asserting the entity called it is asserting on setup.
- **`patch.object(Cls, "name", autospec=True)` over `patch("dotted.path")`.**
  The string form silently no-ops when the path moves.
- **Drive the real setup and unload.** Load the entry with
  `await hass.config_entries.async_setup(entry.entry_id)` and unload with
  `async_unload`; then assert on entity states, the registries, the issue
  registry and `entry.state`. Do not patch `async_setup_entry` or a platform's
  `async_setup_entry` for a test of setup, unload, reload, entities or
  services — the bugs live exactly in that wiring (stale credentials written
  on unload, an `except` that never runs during real setup).
  The one exception is the core pattern for **config-flow** tests: a single
  shared `mock_setup_entry` fixture may stub
  `custom_components.mammotion.async_setup_entry` so a flow test asserts the
  flow result and the created entry's `data`/`unique_id` without a full
  bring-up. A flow test whose subject is what happens *after* the flow (a
  reload, credentials kept or dropped) uses the real lifecycle.
- **Assert on outcomes, not plumbing.** Entity state, a raised
  `HomeAssistantError` with the expected `translation_key`, a `FlowResult`,
  an issue in the issue registry, a service response. A call assertion is
  right only when the call *is* the contract ("does not send to an offline
  mower" is `send.assert_not_awaited()`).
- **One mock per boundary, configured once.** Six mocks to stand up one entity
  is a design note, not a setup to grow.

---

## 6. Time, timers and async determinism

- **Never `time.sleep()`. Never `await asyncio.sleep(<non-zero>)` as
  synchronisation.** Use `await hass.async_block_till_done()` for "let
  everything scheduled run", an `asyncio.Event` the code sets, or
  `asyncio.sleep(0)` for exactly one loop turn.
- **Timers move only when the test moves them.** For `async_track_time_interval`,
  `async_call_later`, debouncers and coordinator `update_interval`:
  `freezer.tick(timedelta(...))` (the `freezer` fixture) then
  `async_fire_time_changed(hass)` then `await hass.async_block_till_done()`.
  Two modules do this today; the rest wait on real time or skip the timer.
- **Freeze the clock** for anything reading `dt_util.utcnow()`,
  `time.time()` or `time.monotonic()` — `freezer` / `freeze_time` for HA time,
  an injected clock (`remote_drive_support.ManualClock`) for library time.
- **Bound every wait.** Any `await` on a future or event the code may never
  resolve is wrapped in `asyncio.wait_for(..., TIMEOUT)` with a module-level
  constant.
- `asyncio_mode = "auto"`: **no `@pytest.mark.asyncio`.**

---

## 7. Regression tests

1. **It failed before the fix.** Write it against the broken code and watch
   it fail. A regression test written after the fix and never seen red is a
   restatement of the implementation.
2. **Its docstring records the defect** — what the code did, what it should
   have done — not the diff.
3. **Named for the behaviour, not the ticket**:
   `test_edit_task_keeps_a_disabled_schedule_disabled`, not `test_issue_913`.
   The issue number may go in the docstring.
4. **Marked `@pytest.mark.regression`** (registered in `pyproject.toml`).
5. **Lives with the module it pins** (§2).

---

## 8. Assertions and isolation

- One behaviour per test; several `assert`s describing one outcome are fine.
- Give a non-obvious assertion a message.
- Assert on logs (`caplog`, scoped to the logger) only when the log is the
  contract.
- No order dependence, no module-level mutable state shared between tests.
- `monkeypatch` for env and attributes, `tmp_path` for files.
- **Nothing reaches the network** — not pymammotion's HTTP, not Agora, not
  DNS. `pytest-homeassistant-custom-component` blocks sockets; do not unblock
  them.
- **Banned:** `print()`, `if __name__ == "__main__":`, commented-out tests,
  `skip`/`xfail` without a reason naming the blocker.

---

## 9. Reaching into privates

`tests_ha/*` has `SLF001` off because some behaviour is only observable on a
private (`coordinator._operation_settings`). Reading a private to assert is
tolerated; ask once whether it should be public. **Setting up** through
privates (assigning `_client`, `_device` by hand) is a builder's job, done
once in a `*_support.py`, so that a constructor change breaks one place.

---

## 10. Home Assistant test idioms

From Home Assistant core's agent instructions, adopted here:

- **Every test parameter has a type annotation**, using the concrete type
  (`HomeAssistant`, `MockConfigEntry`, `FrozenDateTimeFactory`,
  `SnapshotAssertion`), not `Any`.
- **No branching in a test body.** An `if` in a test means two tests: split
  it, or move the difference into the parametrization.
- **Parametrize near-duplicates** with `pytest.param(..., id="...")`. Do not
  parametrize across behaviours — a parameter that flips which assertion
  matters is two tests.
- **Snapshots** (`syrupy`, `snapshot: SnapshotAssertion`, `.ambr` files under
  `tests_ha/snapshots/`) for large, stable structures: diagnostics output,
  entity registry rows for a model, `services.yaml`-shaped responses. Not for
  small values a reader should see inline, and never as a way to accept
  whatever the code currently does: a snapshot update in a diff is reviewed
  like code. Regenerate with `--snapshot-update` and read the diff.
- Hardcoded `entity_id`s are fine; a repeated one becomes a module constant.
- **Translations:** tests load `custom_components/mammotion/translations/en.json`,
  not `strings.json`. This repository keeps `en.json` as an exact copy of
  `strings.json` (there is no `script.translations` here), so after editing
  `strings.json` copy it to `translations/en.json` before running tests, and
  update every other locale (`CLAUDE.md` → Translations).

---

## 11. What must be tested

- **Every bug fix ships with a regression test** meeting §7.
- **Every user-facing action** (entity method, service, button) has a test for
  its failure path asserting a `HomeAssistantError` with a `translation_key`
  — not only the happy path. This is the `action-exceptions` rule, and the
  family behind #914.
- **Every config-flow step and every `errors["base"]` value** is reached by a
  test, including recovery after an error (`config-flow-test-coverage`).
- **Setup failure classes:** transient → `ConfigEntryState.SETUP_RETRY`,
  rejected credentials → reauth flow started, anything else →
  `SETUP_ERROR`. Each one asserted on `entry.state`, not on a mock (#915).
- **Every invariant stated in prose** (`CLAUDE.md`, the
  `ha-integration-knowledge` skill) is fair game to assert mechanically (§14).
- **Not tested here:** pymammotion behaviour, HA core behaviour, getters that
  only return a field.

---

## 12. Running and coverage

```bash
uv run --no-sync python -m pytest tests_ha -q
uv run --no-sync python -m pytest tests_ha/test_lawn_mower_failure_keys.py -q
uv run --no-sync python -m pytest tests_ha -m regression -q
uv run --no-sync python -m pytest tests_ha -q --cov --cov-report=term-missing
```

`pytest-cov` comes in with `pytest-homeassistant-custom-component`. Coverage
is configured in `pyproject.toml` (`[tool.coverage.*]`: source
`custom_components/mammotion`, branch coverage, missing lines shown). CI
(`.github/workflows/tests.yml`) runs the suite with `--cov-fail-under`, set to
the measured total minus one point. **Raise the floor when coverage rises;
never lower it to get a change through** — a change that drops coverage is
missing its tests. Gold-tier `test-coverage` wants >95% for the integration.

The pre-commit `pytest` hook runs the whole suite, so a red suite blocks the
commit.

---

## 13. Automated review

**Every `tests_ha/` file an agent writes or modifies is reviewed by the
`test-reviewer` agent before the work is reported complete.**

- A `PostToolUse` hook on `Write`/`Edit` of any `tests_ha/*.py` queues the file
  and reminds the session (`.claude/hooks/test_review_gate.py`, wired in
  `.claude/settings.json`).
- A `Stop` hook refuses the first stop while the queue is non-empty, listing
  the files.
- Launching `test-reviewer` clears the queue. The queue lives in
  `.claude/state/` (per session; keep it out of git).

The reviewer reads this document, runs the tests, and reports Blocking /
Should fix / Advisory findings citing sections. The author fixes; the
reviewer never rewrites.

---

## 14. Mechanical conventions

Implemented in `tests_ha/meta/`: `test_conventions_schemas.py` (§14.1–14.2),
`test_conventions.py` (§14.3–14.6), `test_conventions_tables.py`
(§14.7–14.9) and `test_conventions_suite.py` (§14.10), sharing
`conventions_support.py` (`assert_ratchet`, ported from Luba-API). Each check
runs over source text, the AST or the shipped JSON — no `hass` — and carries
a baseline of today's offenders as a ratchet: exceeding an entry fails, and so
does undershooting one without tightening it. `pytest tests_ha/meta` answers
"are the conventions intact?". The baselines as written are in §15.

### 14.1 No schema default over a held value (#913)

For every voluptuous schema registered as a service in `services.py`,
`lawn_mower.py` and `camera.py`: no `vol.Optional(key, default=...)` unless
`(service, key)` is in an allow-list carrying the reason. Names the family:
`edit_*`, `modify_*`, `start_*`, `set_*` handlers merge onto stored or
entity-held values, so a filled-in default overwrites them.
`meta/test_conventions_schemas.py` (`_ALLOWED_DEFAULTS`, stale-entry check,
every-service-covered check).

### 14.2 `services.yaml` defaults match the schemas

Every `default:` in `services.yaml` equals the schema's default for that
field, and a field without a schema default has no `default:` there.
In `meta/test_conventions_schemas.py`; `test_services_yaml.py` covers
selectors and translations.

### 14.3 Except clauses are ordered child before parent

For every `try` in `custom_components/mammotion/`, resolve each handler's
exception names (imports resolved to the real classes, tuples and
module-level tuple constants such as `COMMAND_EXCEPTIONS` expanded) and fail
when a handler catches a class that is a subclass of one caught by an earlier
handler of the same `try`. That clause is dead code; the
`except AccountInUseError` branch behind its parent was one. A member that
only arrives through a shared tuple (`*COMMAND_EXCEPTIONS` after an explicit
`except FailedRequestException`) is not flagged unless the whole clause is
shadowed. Baseline: none.

### 14.4 No catch-all that returns a value in setup paths (#915)

In `__init__.py` and `coordinator.py`, an `except Exception` /
`except BaseException` / bare `except` whose body contains a `return` with a
value (or ends the function returning a value, e.g. `return False`) fails,
unless its enclosing function is in an allow-list with the reason (a
best-effort teardown, one device's failure isolated from the others). A
catch-all that logs and re-raises, or raises a `ConfigEntry*` error, passes.
Also flag `except ...: pass` in any function reachable from an entity action
(the action methods, service handlers, and the coordinator methods they and
the descriptions' `*_fn` lambdas call, transitively).

### 14.5 Entity actions raise only `HomeAssistantError` (#914)

AST over the entity platforms and `services.py`: every `await` of a
coordinator command helper (the `async_*` methods on the coordinator classes
that send to the mower, plus `send_command_and_update` and
`async_send_command`) inside an entity action method (`async_press`,
`async_turn_on/off`, `async_select_option`, `async_set_native_value`,
`async_start_mowing`, `async_dock`, `async_pause`, service handlers) must be
either inside a `try` whose handler catches `COMMAND_EXCEPTIONS` and raises a
`HomeAssistantError` subclass, or go through the coordinator helper that
already maps them (the direct-send path in `coordinator.py`). The test derives
the unmapped set rather than listing it: a coordinator method is unmapped when
it awaits `self.manager.*` or the cloud HTTP client outside such a `try`, or
awaits an unmapped method; callbacks handed to `_async_device_call` are its
mapping. A description's `*_fn` lambda calling an unmapped method counts too.
Also: every `raise HomeAssistantError(...)` /
`ServiceValidationError(...)` in the integration passes `translation_domain`
and `translation_key` (the `exception-translations` rule) — baseline the
f-string raises that exist today.

### 14.6 No hand-copied exception lists

No module other than `const.py` defines a tuple that contains three or more
of the classes in `COMMAND_EXCEPTIONS`; use the constant (or a named
extension of it).

### 14.7 `TO_REDACT` covers identifier fields (#919)

Walk the pymammotion models reachable from `diagnostics.py`'s output
(`MowingDevice`, `Device`, the RTK and Spino data classes; dataclass fields
and mashumaro fields recursively), plus every model in
`pymammotion.data.mqtt.mammotion_properties`, which reaches the dump only as
JSON strings inside `mqtt_properties` (#921), and collect every field name matching
`imei|imsi|iccid|mac|ssid|(^|_)ip($|_)|ip_address|token|key|secret|password|serial|sn$|gateway|mask`.
Each must be in `diagnostics.TO_REDACT` or in an allow-list with the reason
(`iot_id`, `identity_id`, `nick_name`, `lat`/`lon` per the comment in
`diagnostics.py`). A new library field fails the test until someone decides.
Complement with a snapshot of a diagnostics dump from a fully populated
fixture device (`tests_ha/test_diagnostics.py`).

### 14.8 Mode tables come from pymammotion (#917)

`NO_REQUEST_MODES` and the other mode/trigger tables the coordinator uses are
the library's objects (`is` identity), and `const.py` defines no tuple or set
of `WorkMode` members. `test_no_request_modes.py` pins `NO_REQUEST_MODES`;
`meta/test_conventions_tables.py` counts every `WorkMode` collection literal
per module (a `Tuple`/`Set`/`List`/`frozenset(...)` of two or more
`WorkMode.*` attributes) and requires every module-level `WorkMode` table to be
a pymammotion object, outside `MODE_TABLES_ALLOWED` (tables the library has
no counterpart for, each with its reason).

### 14.9 Translations in sync

`translations/en.json` equals `strings.json`; every locale has exactly the
keys of `strings.json`; every `translation_key=` literal used in a raise or
an issue exists under `exceptions` / `issues`; every entity `translation_key`
exists under `entity.<platform>`; `icons.json` has an entry for every entity
`translation_key` that declares no `icon` (a `device_class` counts as
declaring one). Key parity and "no English copies" stay in
`test_translations_not_english.py`; the rest is in
`meta/test_conventions_tables.py`, where exception keys also cover
`failure_key=` arguments, `trans_key` assignments and `failure_key` /
`translation_key` parameter defaults.

### 14.10 Suite hygiene (from Luba-API)

No `time.sleep`, no `asyncio.sleep(<non-zero literal>)` outside named helpers,
no `@pytest.mark.asyncio`, no `print()`/`__main__` runners, skip/xfail carry a
reason, module docstring present, module under ~600 lines, every test
parameter annotated, no `MagicMock()` without `spec`/`spec_set` (baselined
per module), no builder name (`make_*`, `_make_*`) defined in two modules,
every `@pytest.mark.regression` test has a docstring.

---

## 15. Legacy debt

Measured 2026-10-02 against a tree mid-edit.

| Debt | Size | Fix |
|---|---|---|
| test modules not named for a source module | 73 of 101 | §2 — new tests go to the mirrored file; rename when a file is next touched |
| source modules with no `test_<module>*` | `binary_sensor`, `device_tracker`, `entity`, `number`, `select`, `sensor`, `update`, `vacuum` (+ `const`, `models`, `geojson_utils`) | §11 |
| timers driven without `async_fire_time_changed` | most timer tests | §6 |
| config-flow tests patching `async_setup_entry` per module | 3 modules | §5 — one shared `mock_setup_entry` fixture |
| repo-root `test_sdp.py`, `test_credential_fallback.py` | 2 | §2 — delete or move |

The meta-test baselines as first written (the constants in `tests_ha/meta/`
are the authority):

| Baseline | Rule | Size |
|---|---|---|
| `UNMAPPED_ACTION_CALLS` | §14.5 | 21 call sites: 11 button `press_fn`, 4 service handlers (`fetch_mow_path`, `svg_*`), 3 switch (remote drive off, Bluetooth / cloud toggles), 3 `update.async_install` |
| `UNTRANSLATED_RAISES` | §14.5 | 1 (`coordinator.py`, an f-string credential-refresh raise) |
| `CATCH_ALL_RETURNS` / `SWALLOWED_IN_ACTIONS` | §14.4 | 0 / 0 (`async_check_stream_expiry` allow-listed: the camera path, not setup) |
| `COPIED_EXCEPTION_TUPLES` | §14.6 | 0 |
| `UNREDACTED_FIELDS` | §14.7 | 0 (`product_key`, `category_key`, `_category_key`, `is_edge_gateway` allow-listed with reasons) |
| `WORKMODE_LITERALS` | §14.8 | 11: `lawn_mower.py` 8, `coordinator.py` 2, `button.py` 1 (`_DROPMOW_MODES`, `ERROR_LOG_READ_MODES` allow-listed) |
| `MISSING_ENTITY_TRANSLATIONS` | §14.9 | 2 (`device_tracker.device_tracker`, `update.update`) |
| `MISSING_ICONS` | §14.9 | 32 keys with neither `icon`, `device_class` nor an icons.json entry |
| `MISSING_EXCEPTION_KEYS` | §14.9 | 0 (repair-issue keys are checked against `issues`, no baseline) |
| `UNSPECCED_MAGICMOCKS` | §14.10 | 265 in 54 modules |
| `UNANNOTATED_PARAMETERS` | §14.10 | 16 in 3 modules |
| `OVERSIZED` | §14.10 | 2 (`test_notifications.py` 734, `test_switch_area_lifecycle.py` 604) |
| `WALL_CLOCK_SLEEPS` | §14.10 | 1 (`test_ble_first_setup.py`) |
| `DUPLICATE_BUILDERS` | §14.10 | `make_coordinator` in 4 modules |
| `UNDOCUMENTED_MODULES`, bare asyncio markers | §14.10 | 0 |

Fix the debt your change touches and shrink its baseline entry; do not fix it
in bulk as a side effect of unrelated work.
