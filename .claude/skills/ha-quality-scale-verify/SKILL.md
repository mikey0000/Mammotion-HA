---
name: ha-quality-scale-verify
description: Verifies that the Mammotion custom integration follows a specific Home Assistant Integration Quality Scale rule, checking whether it implements the required patterns, configurations, or code structures. Use when asked to check a rule (e.g. "check the action-exceptions rule") or to verify the integration reaches a quality tier (Bronze, Silver, Gold, Platinum).
---
<!-- Ported from home-assistant/core .claude/skills/ha-quality-scale-verify/SKILL.md (Apache-2.0, (c) Home Assistant contributors); adapted to a custom integration with no quality_scale.yaml. -->

# Verify Quality Scale Rule

You are verifying whether the Mammotion integration follows a specific quality scale rule. Verify one rule at a time; to check a full tier, verify each of that tier's rules (run in parallel subagents when possible).

The rule ids per tier are the ones hassfest knows (`script/hassfest/quality_scale.py` in home-assistant/core). `docs/quality_scale.md` lists all of them with this integration's current status.

## 1. Fetch rule documentation
Retrieve the official rule documentation from:
`https://raw.githubusercontent.com/home-assistant/developers.home-assistant/refs/heads/master/docs/core/integration-quality-scale/rules/{rule_name}.md`
where `{rule_name}` is the rule identifier (e.g. `config-flow`, `entity-unique-id`, `parallel-updates`).

## 2. Understand rule requirements
Parse the rule documentation to identify:
- Core requirements and mandatory implementations
- Specific code patterns or configurations required
- Common violations and anti-patterns
- Exemption criteria (when a rule might not apply)
- The quality tier this rule belongs to (Bronze, Silver, Gold, Platinum)

## 3. Analyze the integration code
Examine `custom_components/mammotion/`, focusing on:
- `manifest.json` for configuration (there is no `quality_scale` key; this is not a core integration)
- `docs/quality_scale.md` for the recorded rule status (done, todo, exempt)
- Relevant Python modules based on the rule requirements
- `services.yaml`, `strings.json`, `translations/*.json` and `icons.json` as needed
- `tests_ha/` for the rules that are about tests (`config-flow-test-coverage`, `test-coverage`)

Additional sources:
- Integration docs: `README.md` and the wiki at https://github.com/mikey0000/Mammotion-HA/wiki
- PyPI package info: `https://pypi.org/pypi/pymammotion/json`; the library source is the Luba-API repository

## 4. Verification process
- Check whether the rule is marked `done`, `todo`, or `exempt` in `docs/quality_scale.md`
- If marked `exempt`, verify the exemption reason is valid
- If marked `done`, verify the actual implementation matches the requirements
- Identify specific files and code sections that demonstrate compliance or violations
- Look for the exact implementation patterns specified in the rule
- Check for common mistakes, anti-patterns, edge cases, and error handling requirements
- Validate that implementations follow Home Assistant conventions
- Where the rule is mechanically checkable, say which test (existing, or specified in `docs/testing.md` "Mechanical conventions") would pin it

Quality scale rules are cumulative: Bronze rules apply to every integration, Silver to Silver+, and so on.

## 5. Report findings
Report only the rules that have issues. Do not list rules that pass or that are validly exempt. For each rule with an issue, provide:
- **Rule**: The rule identifier and the problem (non-compliance, or an invalid/unjustified exemption)
- **Evidence**: Specific file locations and code showing the violation
- **Recommendation**: Actionable steps to achieve compliance

If no rules have issues, say so in a single line. If the recorded status in `docs/quality_scale.md` is wrong, say what it should be. If you cannot access the rule documentation, clearly state what is missing.
