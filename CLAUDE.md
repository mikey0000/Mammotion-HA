# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build Commands

- Install dependencies: `uv sync`
- Run in environment: `uv run`
- Run tests: `uv run pytest`
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

## Home Assistant Integration Rules

- All imports within the integration must be relative (e.g. `from . import Foo`, `from .services import bar`). Never use `from custom_components.mammotion import ...` — HA loads integrations in a way that makes absolute imports from `custom_components` fail at runtime.

## Translations

- When adding or renaming any entity (sensor, switch, button, number, select, etc.) or an ENUM entity state, you MUST update the translations in **every** language file, not just English.
- The files to keep in sync: `custom_components/mammotion/strings.json` (the source) **and** every file under `custom_components/mammotion/translations/` (`en`, `cs`, `da`, `de`, `fr`, `hu`, `it`, `nl`, `pl`, `ro`, `sl`, `sv`, plus any new locale present in that directory). Treat the directory listing as the source of truth for which languages exist rather than this hard-coded list.
- Translate the entity `name` and every ENUM `state` value into each language's own language — do not copy the English text into the other locales as a placeholder.
- Also add an icon entry in `custom_components/mammotion/icons.json` for the new entity where appropriate.
- After editing, confirm every JSON file still parses and that the new key (with all its `state` values) is present in each file before considering the change complete.
