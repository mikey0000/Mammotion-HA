---
name: ha-review
description: Reviews Home Assistant code changes in the Mammotion integration and provides constructive feedback. Should be used when a review is requested to provide a consistent review behavior and output format. This skill can be used for code reviews in general, not just for GitHub pull requests.
---
<!-- Ported from home-assistant/core .claude/skills/ha-review/SKILL.md (Apache-2.0, (c) Home Assistant contributors); base branch, quality-scale file and the "Recurring defects" list are local. -->

# Review Code Changes

## Scope:
- Unless instructed otherwise, review the full changes (the ones from the branch plus uncommitted ones) against the target branch. Resolve the base to an available ref (prefer `origin/<base>`, then local `<base>`) and review `git diff "$(git merge-base "$BASE_REF" HEAD)"`; use `main` as the default base.

## Analyze the code changes for:
- Code quality and style consistency
- Potential bugs or issues
- Performance implications
- Security concerns
- Test coverage
- Documentation updates if needed

Read the `ha-integration-knowledge` skill first; its "In this repository" list is the checklist for this codebase.

## Recurring defects (check every diff for these):
- A `vol.Optional(..., default=...)` added to a schema whose handler merges onto stored or entity-held values; a `services.yaml` `default:` the schema does not apply.
- A value read from coordinator data before an `await` and acted on after it.
- A user-facing action path (entity method, service handler, button press) that can end in a log line, `pass`, or `return` on failure instead of a translated `HomeAssistantError`; a raw library exception that can escape; a hand-copied exception tuple instead of `COMMAND_EXCEPTIONS`.
- `except Exception` that returns a value (bool, None, empty list) in setup or login paths.
- An `except` clause unreachable because an earlier clause catches its parent class.
- A raw firmware field turned into state without validation; a mode/trigger table copied from pymammotion.
- A new pymammotion model field with an identifier-like name and no `TO_REDACT` entry.
- Unload or reload writing credentials it no longer owns.
- A config-flow step mapping login errors to `errors["base"]` by itself instead of through the shared helper.
- A new or renamed entity, state, exception or issue key missing from `strings.json`, any `translations/*.json`, or `icons.json`.
- Logic that belongs in pymammotion (protocol, retry, transport selection) added here.

## Quality scale:
- If the changes touch `docs/quality_scale.md`, or implement or regress a rule listed there, run a subagent to verify those rules following the `ha-quality-scale-verify` skill.
- Include the verification results in the final review comments.

## Tests:
- If the changes touch `tests_ha/`, run the `test-reviewer` agent over the touched test files and include its verdict.

## Verification:
- After the review, run parallel subagents for each finding to double-check it.
- Spawn up to a maximum of 10 parallel subagents at a time.
- Gather the results from the subagents and summarize them in the final review comments.

## IMPORTANT:
- Just review. DO NOT make any changes.
- Be constructive and specific in your comments.
- Suggest improvements where appropriate.
- No need to run tests or linters, just review the code changes.
- No need to highlight things that are already good.

## Output format:
- List specific comments for each file/line that needs attention.
- In the end, summarize with an overall assessment (approve, request changes, or comment) and bullet point list of changes suggested, if any.
  - Example output:
    ```
    Overall assessment: request changes.
    - [CRITICAL] coordinator.py:143 - User command failure ends in a log line
    - [PROBLEM] services.py:87 - Schema default overwrites the stored plan
    - [SUGGESTION] tests_ha/test_x.py:45 - Improve x variable name
    ```
  - Make sure to include the file and line number when possible in the bullet points.
