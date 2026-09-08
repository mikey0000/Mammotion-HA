"""The manual user step records the mower picked from the dropdown before the credentials step."""

import ast
from pathlib import Path

_flow_path = (
    Path(__file__).parent.parent / "custom_components" / "mammotion" / "config_flow.py"
)
_source = _flow_path.read_text()
_tree = ast.parse(_source)


def _method(name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(_tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def test_user_step_stores_selected_address_as_ble_device() -> None:
    src = ast.get_source_segment(_source, _method("async_step_user"))
    # Without this the wifi step sees no BLE device and rejects a BLE-only setup.
    assert "user_input.get(CONF_ADDRESS)" in src
    assert "self._config = {CONF_BLE_DEVICES:" in src
    assert "self._discovered_device = bluetooth.async_ble_device_from_address(" in src
    assert src.index("self._config = {CONF_BLE_DEVICES:") < src.index(
        "return await self.async_step_wifi(user_input)"
    )


def test_wifi_step_names_entry_from_selected_device_when_ble_object_is_gone() -> None:
    wifi_src = ast.get_source_segment(_source, _method("async_step_wifi"))
    helper_src = ast.get_source_segment(_source, _method("_ble_device_name"))
    assert "CONF_DEVICE_NAME: self._ble_device_name()" in wifi_src
    assert "title=self._ble_device_name()" in wifi_src
    assert "self._config.get(CONF_BLE_DEVICES, {})" in helper_src
