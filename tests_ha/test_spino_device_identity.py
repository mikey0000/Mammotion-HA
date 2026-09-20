"""A Spino must present as its variant, not as a raw Aliyun product key (issue #892).

The version in ``tests/`` could not import the real ``DeviceType`` — the stubbed
suite replaces ``pymammotion`` wholesale — so it read the integration's source
and ran ``device_serial_number`` against a stand-in type.  Here the real type
table, the real coordinator and the real device registry are used.

``device_info`` itself is asserted field by field in
``test_spino_device_info.py``; this file covers the identity the coordinator and
the setup path feed into it.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.data.model.device import PoolCleanerDevice
from pymammotion.utility.device_type import DeviceType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _build_device_list
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionSpinoCoordinator
from custom_components.mammotion.entity import (
    MammotionBaseSpinoEntity,
    device_serial_number,
)

_NAME = "Spino-E1C36JT4"
_E1_PRODUCT_KEY = "a15Cq8FbCh1"
_PILE_PRODUCT_KEY = "GJzsmaVk5za"


def _device(name: str, product_key: str) -> Device:
    """Return an account device record as the cloud list hands it over."""
    return Device(
        gmt_modified=0,
        node_type="DEVICE",
        device_name=name,
        product_name=name,
        status=1,
        identity_id=name,
        net_type="WIFI",
        category_key="",
        product_key=product_key,
        is_edge_gateway=False,
        category_name="",
        identity_alias=name,
        iot_id=f"iot-{name}",
        bind_time=0,
        owned=1,
        thing_type="DEVICE",
    )


def _coordinator(
    name: str = _NAME, product_key: str = _E1_PRODUCT_KEY
) -> MammotionSpinoCoordinator:
    """Build the real coordinator; only the client and the store are stand-ins."""
    coordinator = MammotionSpinoCoordinator.__new__(MammotionSpinoCoordinator)
    coordinator.device_name = name
    coordinator.unique_name = name
    coordinator.device = _device(name, product_key)
    coordinator.data = PoolCleanerDevice()
    coordinator.hass = MagicMock()
    coordinator._subscriptions = []
    coordinator.manager = MagicMock()
    coordinator.async_subscribe_status = AsyncMock()
    coordinator.async_send_and_wait = AsyncMock()
    coordinator.async_fetch_pool_map = AsyncMock()
    coordinator.async_fetch_pool_line = AsyncMock()
    return coordinator


def test_serial_number_drops_the_device_type_prefix() -> None:
    """The user's card should read C36JT4, not Spino-E1C36JT4 or E1C36JT4."""
    assert device_serial_number(_NAME, DeviceType.SWIMMINGPOOL_E1) == "C36JT4"


def test_serial_number_handles_a_multi_prefix_type() -> None:
    """SWIMMINGPOOL_SP claims both "Spino-SP" and "Spino-S1"."""
    device_type = DeviceType.value_of_str("Spino-S1ABC123")
    assert device_type is DeviceType.SWIMMINGPOOL_SP
    assert device_serial_number("Spino-S1ABC123", device_type) == "ABC123"


def test_serial_number_keeps_an_unrecognised_name_whole() -> None:
    """Better a full name than a name truncated at the wrong place."""
    assert device_serial_number("Puddle-9000", DeviceType.SWIMMINGPOOL_E1) == (
        "Puddle-9000"
    )


def test_the_coordinator_resolves_the_variant_from_the_product_key() -> None:
    """The pool models are sold under names the prefix table does not cover."""
    assert _coordinator("PoolBot").device_type is DeviceType.SWIMMINGPOOL_E1


def test_the_coordinator_resolves_the_variant_from_the_name() -> None:
    """A BLE-only cleaner has no product key at all."""
    assert _coordinator(product_key="").device_type is DeviceType.SWIMMINGPOOL_E1


async def test_setup_reseeds_the_cloud_identity() -> None:
    """A restored PoolCleanerDevice is bare, so its name would stay empty."""
    coordinator = _coordinator()
    handle = MagicMock()
    handle.snapshot.raw = PoolCleanerDevice()
    coordinator.manager.pool_cleaner_device.return_value = handle

    await coordinator._async_setup()

    restored = handle.snapshot.raw
    assert restored.product_key == _E1_PRODUCT_KEY
    assert restored.iot_id == f"iot-{_NAME}"
    assert restored.name == _NAME
    handle.state_machine.apply.assert_called_once_with(restored, handle.availability)


def test_the_coordinator_uses_the_pool_cleaner_accessor() -> None:
    """A pool cleaner is not a mower, even where the lookup is the same.

    Structural: ``manager.mower`` returning None for a Spino is what the
    behavioural tests would show, and that is the library's business, not this
    integration's — the contract here is that the class never asks for it.
    """
    source = (
        Path(__file__).parent.parent
        / "custom_components"
        / "mammotion"
        / "coordinator.py"
    ).read_text()
    tree = ast.parse(source)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "MammotionSpinoCoordinator"
    )
    body = ast.get_source_segment(source, node) or ""
    assert "self.manager.mower(" not in body
    assert "self.manager.pool_cleaner_device(" in body


async def test_the_app_nickname_becomes_the_device_name(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """Only while the user has not renamed the device in Home Assistant.

    Driven through ``async_added_to_hass``, which is the only thing that calls
    the cleanup.
    """
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, _NAME)}
    )
    coordinator = _coordinator()
    coordinator.device.nick_name = "Pool robot"
    coordinator.async_add_listener = MagicMock(return_value=MagicMock())
    entity = MammotionBaseSpinoEntity.__new__(MammotionBaseSpinoEntity)
    entity.coordinator = coordinator
    entity.coordinator_context = None
    entity.hass = hass

    await entity.async_added_to_hass()

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, _NAME), entry.entry_id
    )
    assert device.name_by_user == "Pool robot"


async def test_a_user_chosen_name_is_left_alone(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """The app nickname is a default, not an override."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    created = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, _NAME)}
    )
    device_registry.async_update_device(created.id, name_by_user="Deep end")
    coordinator = _coordinator()
    coordinator.device.nick_name = "Pool robot"
    entity = MammotionBaseSpinoEntity.__new__(MammotionBaseSpinoEntity)
    entity.coordinator = coordinator
    entity.hass = hass

    entity._cleanup_stale_connections()

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, _NAME), entry.entry_id
    )
    assert device.name_by_user == "Deep end"


@pytest.mark.parametrize(
    ("name", "product_key"),
    [(_NAME, _E1_PRODUCT_KEY), ("PoolBot", _E1_PRODUCT_KEY)],
)
def test_a_pool_cleaner_is_routed_to_the_spino_platforms(
    name: str, product_key: str
) -> None:
    """Name or product key — either identifies the cleaner."""
    mammotion = MagicMock()
    mammotion.aliyun_device_list = [_device(name, product_key)]
    mammotion.mammotion_device_list = []

    _mowers, _rtk, spinos = _build_device_list(mammotion)

    assert [device.device_name for device in spinos] == [name]


def test_the_charging_pile_is_not_a_pool_cleaner() -> None:
    """SD_PX is the PC210's pile; is_swimming_pool() claims it anyway."""
    pile = _device("PoolBotPile", _PILE_PRODUCT_KEY)
    mammotion = MagicMock()
    mammotion.aliyun_device_list = [pile]
    mammotion.mammotion_device_list = []

    mowers, rtk, spinos = _build_device_list(mammotion)

    assert DeviceType.value_of_str(pile.device_name, pile.product_key) is (
        DeviceType.SD_PX
    )
    assert spinos == []
    assert mowers == []
    assert rtk == []
