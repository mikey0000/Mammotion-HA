"""Schedules can be read back, not just written (issue #890).

``set_task_enabled`` writes the flag but nothing surfaced it, so an automation
could not reconcile Home Assistant with the mower or confirm a change landed.
Two additions: the flag as a task-button attribute, and ``get_tasks`` returning
the whole set as a service response.
"""

import ast
import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"


def _function(src: str, name: str) -> str:
    tree = ast.parse(src)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )
    return ast.get_source_segment(src, node)


def test_the_flag_is_a_property_not_a_snapshot() -> None:
    """Set in __init__ it would report whatever was true at setup."""
    src = (_ROOT / "button.py").read_text()
    body = _function(src, "extra_state_attributes")
    assert "plan.is_enabled()" in body
    init = _function(src, "__init__")
    assert "_attr_extra_state_attributes" not in init


def test_the_task_id_attribute_is_preserved() -> None:
    """Additive change: anything relying on task_id must keep working."""
    body = _function((_ROOT / "button.py").read_text(), "extra_state_attributes")
    assert '"task_id"' in body


def test_a_missing_plan_yields_no_flag() -> None:
    """``data`` is absent between entity creation and the first refresh."""
    src = (_ROOT / "button.py").read_text()
    helper = _function(src, "_plan")
    assert "if data is None:" in helper
    body = _function(src, "extra_state_attributes")
    assert "if plan is not None:" in body


def test_get_tasks_returns_a_response() -> None:
    """The maintainer's preference: readable in the same script that writes."""
    src = (_ROOT / "services.py").read_text()
    assert 'SERVICE_GET_TASKS = "get_tasks"' in src
    registration = src[src.index("SERVICE_GET_TASKS,") :]
    assert "SupportsResponse.ONLY" in registration[:400]


def test_get_tasks_covers_both_device_kinds() -> None:
    """Task services target mowers and Spinos alike."""
    body = _function((_ROOT / "services.py").read_text(), "handle_get_tasks")
    assert "_mower_task_info" in body
    assert "_spino_task_info" in body


def test_the_response_reports_the_enable_flag() -> None:
    """Without it the service answers the wrong question."""
    src = (_ROOT / "services.py").read_text()
    assert '"enabled": plan.is_enabled()' in _function(src, "_mower_task_info")
    assert '"enabled": plan.enabled' in _function(src, "_spino_task_info")


def test_the_response_task_id_matches_the_button_attribute() -> None:
    """So a script can line a response row up with the entity it came from."""
    src = (_ROOT / "services.py").read_text()
    assert '"task_id": plan.plan_id' in _function(src, "_mower_task_info")
    assert '"task_id": str(plan.jobid)' in _function(src, "_spino_task_info")


def test_large_hashes_survive_the_websocket() -> None:
    """zone_hashs exceed JS Number.MAX_SAFE_INTEGER."""
    body = _function((_ROOT / "services.py").read_text(), "handle_get_tasks")
    assert "_stringify_large_ints" in body


def test_the_service_is_declared_and_translated() -> None:
    """services.yaml plus every locale."""
    services = yaml.safe_load((_ROOT / "services.yaml").read_text())
    assert "get_tasks" in services
    assert services["get_tasks"]["target"]["entity"]["integration"] == "mammotion"
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    english = json.loads((_ROOT / "translations" / "en.json").read_text())
    for path in files:
        entry = json.loads(path.read_text())["services"]["get_tasks"]
        assert entry["name"] and entry["description"], path
        if path.stem not in ("en", "strings"):
            assert (
                entry["description"] != english["services"]["get_tasks"]["description"]
            ), path
