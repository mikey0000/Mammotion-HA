"""The Spino device card, asserted against real Home Assistant objects.

The equivalent check in ``tests/`` can only read the source and confirm the
right expressions appear.  Here the property runs and the resulting
``DeviceInfo`` is inspected, so the test would catch a wrong value rather than
just a missing token — the gap both code reviews of this work called out.

A real user's card read ``a15Cq8FbCh1`` as the model and
``Spino-E1C36JT4`` as the serial (Mammotion-HA, issue #892's sibling report).
"""

from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    CONNECTION_NETWORK_MAC,
)
from pymammotion.data.model.device import PoolCleanerDevice
from pymammotion.utility.device_type import DeviceType

from custom_components.mammotion.entity import MammotionBaseSpinoEntity

_NAME = "Spino-E1C36JT4"
_PRODUCT_KEY = "a15Cq8FbCh1"


def _entity(
    device: PoolCleanerDevice | None = None, product_model: str = ""
) -> MammotionBaseSpinoEntity:
    """Build the entity with a stand-in coordinator; the property itself is real."""
    coordinator = MagicMock()
    coordinator.data = device if device is not None else PoolCleanerDevice()
    coordinator.device_name = _NAME
    coordinator.unique_name = _NAME
    coordinator.device_type = DeviceType.value_of_str(_NAME, _PRODUCT_KEY)
    coordinator.device.product_model = product_model
    entity = MammotionBaseSpinoEntity.__new__(MammotionBaseSpinoEntity)
    entity.coordinator = coordinator
    return entity


def test_the_model_is_the_hardware_not_the_product_key() -> None:
    """The card showed the raw Aliyun key because model resolved to an empty name."""
    assert _entity().device_info["model"] == "Spino E1"


def test_the_cloud_model_name_wins_when_the_account_supplies_one() -> None:
    """Mirrors how the mower prefers product_model."""
    assert _entity(product_model="Spino E1 Pro").device_info["model"] == "Spino E1 Pro"


def test_the_product_key_is_no_longer_exposed() -> None:
    """It was surfacing as model_id."""
    assert "model_id" not in _entity().device_info


def test_the_serial_drops_the_type_prefix() -> None:
    """The whole device name was being shown as the serial."""
    assert _entity().device_info["serial_number"] == "C36JT4"


def test_connections_are_empty_until_the_device_reports_its_macs() -> None:
    """PoolStateReducer does not populate them yet, so this must not invent any."""
    assert _entity().device_info["connections"] == set()


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("bt_mac", "AA:BB:CC:DD:EE:FF", (CONNECTION_BLUETOOTH, "aa:bb:cc:dd:ee:ff")),
        (
            "wifi_mac",
            "11:22:33:44:55:66",
            (CONNECTION_NETWORK_MAC, "11:22:33:44:55:66"),
        ),
    ],
)
def test_a_reported_mac_becomes_a_registry_connection(
    field: str, value: str, expected: tuple[str, str]
) -> None:
    """Normalised through HA's own format_mac, as the mower's are."""
    device = PoolCleanerDevice()
    setattr(device, field, value)
    assert expected in _entity(device).device_info["connections"]


def test_the_identifiers_and_area_are_unchanged() -> None:
    """The fix must not re-key the device and orphan its entities."""
    info = _entity().device_info
    assert info["identifiers"] == {("mammotion", _NAME)}
    assert info["suggested_area"] == "Pool"
    assert info["manufacturer"] == "Mammotion"
