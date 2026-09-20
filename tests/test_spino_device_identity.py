"""A Spino must present as its variant, not as a raw Aliyun product key.

``conftest.py`` replaces ``pymammotion`` and ``custom_components.mammotion.entity``
with stubs, so neither the real ``DeviceType`` nor the real entity class can be
imported here.  These read the integration's own source instead (and the
installed pymammotion's, for the fields it must supply), except for
``device_serial_number``, whose logic is pure enough to execute on its own.
"""

import ast
from importlib.metadata import distribution
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"


def _node(src: str, name: str) -> ast.AST:
    tree = ast.parse(src)
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef))
        and n.name == name
    )


def _source(src: str, name: str) -> str:
    return ast.get_source_segment(src, _node(src, name)) or ""


def _member(src: str, class_name: str, member: str) -> str:
    cls = _node(src, class_name)
    node = next(
        n
        for n in ast.iter_child_nodes(cls)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == member
    )
    return ast.get_source_segment(src, node) or ""


def _pymammotion_source(relative: str) -> str:
    """Return a source file of the *installed* pymammotion, stubs bypassed."""
    root = Path(str(distribution("pymammotion").locate_file("pymammotion")))
    return (root / relative).read_text()


class _FakeDeviceType:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_name(self) -> str:
        return self._name


def _device_serial_number(device_name: str, name: str) -> str:
    """Run the integration's own helper against a stand-in DeviceType."""
    namespace: dict[str, object] = {}
    exec(_source((_ROOT / "entity.py").read_text(), "device_serial_number"), namespace)  # noqa: S102
    helper = namespace["device_serial_number"]
    return helper(device_name, _FakeDeviceType(name))  # type: ignore[operator, no-any-return]


def test_serial_number_drops_the_device_type_prefix() -> None:
    """The user's card should read C36JT4, not Spino-E1C36JT4 or E1C36JT4."""
    assert _device_serial_number("Spino-E1C36JT4", "Spino-E1") == "C36JT4"


def test_serial_number_handles_a_multi_prefix_type() -> None:
    """SWIMMINGPOOL_SP claims both "Spino-SP" and "Spino-S1"."""
    assert _device_serial_number("Spino-S1ABC123", "Spino-SP,Spino-S1") == "ABC123"


def test_serial_number_keeps_an_unrecognised_name_whole() -> None:
    """Better a full name than a name truncated at the wrong place."""
    assert _device_serial_number("Puddle-9000", "Spino-E1") == "Puddle-9000"


def test_the_coordinator_resolves_the_variant() -> None:
    """Name or product key — either identifies the Spino model."""
    body = _member(
        (_ROOT / "coordinator.py").read_text(),
        "MammotionSpinoCoordinator",
        "device_type",
    )
    assert "DeviceType.value_of_str(self.device_name, self.device.product_key)" in body


def test_setup_reseeds_the_cloud_identity() -> None:
    """A restored PoolCleanerDevice is bare, so its name would stay empty."""
    body = _member(
        (_ROOT / "coordinator.py").read_text(),
        "MammotionSpinoCoordinator",
        "_async_setup",
    )
    for line in (
        "updated.product_key = self.device.product_key",
        "updated.iot_id = self.device.iot_id",
        "updated.name = self.device.device_name",
        "handle.state_machine.apply(updated, handle.availability)",
    ):
        assert line in body


def test_the_coordinator_uses_the_pool_cleaner_accessor() -> None:
    """A pool cleaner is not a mower, even where the lookup is the same."""
    body = _source((_ROOT / "coordinator.py").read_text(), "MammotionSpinoCoordinator")
    assert "self.manager.mower(" not in body
    assert "self.manager.pool_cleaner_device(" in body


def test_device_info_shows_a_model_not_a_product_key() -> None:
    """a15Cq8FbCh1 means nothing to a user; "Spino E1" does."""
    body = _member(
        (_ROOT / "entity.py").read_text(), "MammotionBaseSpinoEntity", "device_info"
    )
    assert "model_id" not in body
    assert "self.coordinator.device.product_model" in body
    assert 'device_type.get_model().replace("-", " ")' in body
    assert "device_serial_number(" in body


def test_device_info_registers_the_macs_it_has() -> None:
    """An empty MAC is not a connection."""
    body = _member(
        (_ROOT / "entity.py").read_text(), "MammotionBaseSpinoEntity", "device_info"
    )
    assert 'if spino_device.bt_mac != "":' in body
    assert 'if spino_device.wifi_mac != "":' in body
    assert "connections=connections" in body


def test_the_app_nickname_becomes_the_device_name() -> None:
    """Only while the user has not renamed the device in Home Assistant."""
    src = (_ROOT / "entity.py").read_text()
    assert "self._cleanup_stale_connections()" in _member(
        src, "MammotionBaseSpinoEntity", "async_added_to_hass"
    )
    body = _member(src, "MammotionBaseSpinoEntity", "_cleanup_stale_connections")
    assert "nick_name = self.coordinator.device.nick_name" in body
    assert 'update_kwargs["name_by_user"] = nick_name' in body


def test_the_charging_pile_is_not_a_pool_cleaner() -> None:
    """SD_PX is the PC210's pile; is_swimming_pool() claims it anyway."""
    body = _source((_ROOT / "__init__.py").read_text(), "_build_device_list")
    assert "is not DeviceType.SD_PX" in body
