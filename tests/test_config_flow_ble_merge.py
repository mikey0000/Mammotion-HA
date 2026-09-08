"""Config-flow structure for per-device BLE: discovery merges into any entry, reconfigure re-keys the entry."""

import ast
from pathlib import Path

_flow_path = (
    Path(__file__).parent.parent / "custom_components" / "mammotion" / "config_flow.py"
)
_tree = ast.parse(_flow_path.read_text())


def _method(name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(_tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def test_bluetooth_discovery_no_longer_requires_a_cloud_account() -> None:
    src = ast.get_source_segment(_flow_path.read_text(), _method("check_and_update_bluetooth_device"))
    assert "CONF_ACCOUNT_ID" not in src
    assert "entry.unique_id" in src
    assert "CONF_BLE_DEVICES" in src  # matched by configured device name first


def test_reconfigure_makes_the_account_the_entry_identity() -> None:
    fn = _method("async_step_reconfigure")
    src = ast.get_source_segment(_flow_path.read_text(), fn)
    assert "async_set_unique_id(" in src
    assert "merged_into_existing_account" in src
    assert "temp_client.to_cache()" in src
    # Credentials are persisted on success only — never from a finally block.
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            for stmt in node.finalbody:
                assert "store_cloud_credentials" not in ast.dump(stmt)
    assert "store_cloud_credentials" not in src


def test_removing_the_account_strips_every_cloud_key() -> None:
    src = ast.get_source_segment(_flow_path.read_text(), _method("async_step_reconfigure"))
    for key in ("CONF_ACCOUNTNAME", "CONF_PASSWORD", "CONF_ACCOUNT_ID", "*CREDENTIAL_CACHE_KEYS"):
        assert key in src
