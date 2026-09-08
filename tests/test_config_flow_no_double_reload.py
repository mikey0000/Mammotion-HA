"""Regression test: bluetooth discovery must schedule at most one entry reload.

Bug: ``async_step_bluetooth`` / ``async_step_bluetooth_confirm`` call
``check_and_update_bluetooth_device``, which calls ``async_schedule_reload`` when
it learns a new BLE address, and then call ``_abort_if_unique_id_configured``
with ``updates=``.  That helper defaults to ``reload_on_update=True``, so a
change to CONF_BLE_DEVICES scheduled a *second* reload of an already-loaded
entry.

Each reload runs ``async_setup_entry`` again, which builds a fresh
MammotionClient and logs in.  Two live clients connect to the broker with the
same client_id, the broker rejects both with "Not authorized", and pymammotion
reads that as a credential failure and gives up on cloud MQTT for the rest of
the process.

The config flow module can't be imported under the stub harness in conftest.py
(it shadows homeassistant.helpers), so the invariant is checked against the
source tree instead.
"""

import ast
from pathlib import Path

import pytest

_CONFIG_FLOW = (
    Path(__file__).parent.parent / "custom_components" / "mammotion" / "config_flow.py"
)


def _abort_calls_with_updates() -> list[ast.Call]:
    """Return every _abort_if_unique_id_configured call that passes updates=."""
    tree = ast.parse(_CONFIG_FLOW.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_abort_if_unique_id_configured"
        and any(kw.arg == "updates" for kw in node.keywords)
    ]


def test_update_calls_are_present() -> None:
    """Guard the guard: the calls this test constrains must still exist."""
    assert len(_abort_calls_with_updates()) >= 2


@pytest.mark.parametrize("index", range(2))
def test_entry_data_update_does_not_reload(index: int) -> None:
    """Writing CONF_BLE_DEVICES must not trigger a reload of its own."""
    call = _abort_calls_with_updates()[index]
    reload_kwarg = next(
        (kw for kw in call.keywords if kw.arg == "reload_on_update"), None
    )

    assert reload_kwarg is not None, (
        f"line {call.lineno}: _abort_if_unique_id_configured(updates=...) must pass "
        "reload_on_update=False"
    )
    assert reload_kwarg.value.value is False


def test_ble_devices_merge_tolerates_missing_key() -> None:
    """Entries with no CONF_BLE_DEVICES must not blow up the merge.

    ``**entry.data.get(CONF_BLE_DEVICES, None)`` raises TypeError for a
    Wi-Fi-only entry, which aborts the discovery flow.
    """
    tree = ast.parse(_CONFIG_FLOW.read_text())
    defaults = [
        node.args[1]
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "CONF_BLE_DEVICES"
    ]

    assert defaults, "expected CONF_BLE_DEVICES lookups with an explicit default"
    for default in defaults:
        assert not (
            isinstance(default, ast.Constant) and default.value is None
        ), "CONF_BLE_DEVICES default must be {} so ** unpacking cannot raise"
