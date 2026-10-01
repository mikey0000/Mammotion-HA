#!/usr/bin/env python3
"""Queue test files an agent touches and refuse the first stop until they are reviewed.

Ported from Luba-API. Wired in .claude/settings.json:

* ``PostToolUse`` on Write/Edit/MultiEdit — a Python file under ``tests_ha/`` joins this
  session's review queue and the session is reminded.
* ``PostToolUse`` on Task — launching the ``test-reviewer`` agent clears it.
* ``Stop`` — a non-empty queue blocks the stop once, naming the files.

The queue is cleared when it blocks, so the gate can never loop: it is a
reminder with teeth, not a lock.  The rules it defends are ``docs/testing.md``.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

# Every module there counts: the *_support.py builders and conftest.py shape what the tests can see.
TEST_FILE = re.compile(r"(^|/)tests_ha/.*\.py$")
REVIEWER = "test-reviewer"
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
AGENT_TOOLS = {"Task", "Agent"}


def queue_path(payload: dict) -> pathlib.Path:
    """Return this session's queue file, creating its directory."""
    project = pathlib.Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    session = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or "nosession"))
    directory = project / ".claude" / "state"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"test-review-queue-{session}.txt"


def read_queue(path: pathlib.Path) -> list[str]:
    """Return the queued file paths."""
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line.strip()]


def on_post_tool_use(payload: dict) -> int:
    """Queue a touched test file, or clear the queue when the reviewer launches."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    path = queue_path(payload)

    if tool in AGENT_TOOLS:
        if tool_input.get("subagent_type") == REVIEWER:
            path.unlink(missing_ok=True)
        return 0

    if tool not in EDIT_TOOLS:
        return 0

    edited = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not edited or not TEST_FILE.search(edited):
        return 0

    queued = read_queue(path)
    if edited in queued:
        return 0
    path.write_text("\n".join([*queued, edited]) + "\n")

    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        f"{edited} is queued for mandatory test review. Before reporting this work "
                        f"complete, launch the {REVIEWER} agent over the test files you touched "
                        "(Agent tool, subagent_type='test-reviewer') and fix its blocking findings. "
                        "The rubric is docs/testing.md."
                    ),
                }
            }
        )
        + "\n"
    )
    return 0


def on_stop(payload: dict) -> int:
    """Block the first stop while files are queued."""
    if payload.get("stop_hook_active"):
        return 0
    path = queue_path(payload)
    queued = read_queue(path)
    if not queued:
        return 0
    path.unlink(missing_ok=True)
    listed = "\n".join(f"  - {f}" for f in queued)
    sys.stderr.write(
        "These test files were written or modified this session and have not been reviewed:\n"
        f"{listed}\n"
        f"Launch the {REVIEWER} agent (Agent tool, subagent_type='{REVIEWER}') over them, fix every "
        "blocking finding, then finish. If the user asked you to skip the review, say so and stop.\n"
    )
    return 2


def main() -> int:
    """Dispatch on the hook event."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    event = payload.get("hook_event_name")
    if event == "PostToolUse":
        return on_post_tool_use(payload)
    if event == "Stop":
        return on_stop(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
