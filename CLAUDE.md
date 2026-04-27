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
