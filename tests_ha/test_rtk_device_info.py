"""The RTK base-station device card, asserted against a real ``DeviceInfo``.

The card read ``a1Nc68bGZzX`` where the model belongs.  ``RTKBaseStationDevice``
carries no product name — its ``name`` is never populated by the reducer — so
``model`` resolved to an empty string and Home Assistant fell back to showing
``model_id``, which was set to the raw Aliyun product key.  The account record
does carry the real one: this base station reports
``productModel: "ReferenceStation"``.

Same shape as the Spino report in [[test_spino_device_info]].
"""

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    CONNECTION_NETWORK_MAC,
)
from pymammotion.data.model.device import RTKBaseStationDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionRTKCoordinator
from custom_components.mammotion.entity import MammotionBaseRTKEntity

_NAME = "RTKBAU242721575"
_PRODUCT_KEY = "a1Nc68bGZzX"
_PRODUCT_MODEL = "ReferenceStation"


def _entity(
    device: RTKBaseStationDevice | None = None, product_model: str = _PRODUCT_MODEL
) -> MammotionBaseRTKEntity:
    """Build the entity with a stand-in coordinator; the property itself is real."""
    coordinator = MagicMock()
    coordinator.data = device if device is not None else RTKBaseStationDevice()
    coordinator.device_name = _NAME
    coordinator.unique_name = _NAME
    coordinator.device.product_model = product_model
    coordinator.device.product_key = _PRODUCT_KEY
    entity = MammotionBaseRTKEntity.__new__(MammotionBaseRTKEntity)
    entity.coordinator = coordinator
    return entity


def test_the_model_comes_from_the_account_record() -> None:
    """What the user actually sees on the card's second line."""
    assert _entity().device_info["model"] == _PRODUCT_MODEL


def test_the_product_key_is_not_presented_as_a_model() -> None:
    """The regression: a1Nc68bGZzX stood where the model belongs."""
    info = _entity().device_info

    assert _PRODUCT_KEY not in str(info.get("model"))
    assert info.get("model_id") is None


def test_a_station_with_no_product_model_shows_no_model_at_all() -> None:
    """Better an empty row than the Aliyun key dressed up as hardware."""
    info = _entity(product_model="").device_info

    assert info.get("model") is None


def test_a_name_on_the_state_model_is_still_preferred_over_nothing() -> None:
    """Some stations may populate it; it beats falling through to None."""
    device = RTKBaseStationDevice()
    device.name = "Reference Station"

    assert _entity(device, product_model="").device_info["model"] == "Reference Station"


def test_the_rest_of_the_card_is_unchanged() -> None:
    """The fix touches the model only."""
    device = RTKBaseStationDevice()
    device.bt_mac = "34:b7:da:6f:7f:ee"
    device.wifi_mac = "34:b7:da:6f:7f:ec"
    device.device_version = "1.15.1.1"
    info = _entity(device).device_info

    assert info["name"] == _NAME
    assert info["serial_number"] == _NAME
    assert info["manufacturer"] == "Mammotion"
    assert info["sw_version"] == "1.15.1.1"
    assert (CONNECTION_BLUETOOTH, device.bt_mac) in info["connections"]
    assert (CONNECTION_NETWORK_MAC, device.wifi_mac) in info["connections"]


async def test_a_firmware_version_that_arrives_late_reaches_the_card(
    hass: HomeAssistant,
) -> None:
    """``device_info`` is read once at registration; an RTK reports later than that.

    The base station returns deviceVersion over MQTT well after setup, so the
    registry entry kept the empty string it was created with and the card
    showed no firmware at all.
    """
    coordinator = MammotionRTKCoordinator.__new__(MammotionRTKCoordinator)
    coordinator.hass = hass
    coordinator.unique_name = _NAME
    coordinator.data = RTKBaseStationDevice(name=_NAME)
    coordinator.data.device_version = "1.15.1.1"

    registry = dr.async_get(hass)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _NAME)},
        name=_NAME,
    )
    assert not device.sw_version

    coordinator._sync_firmware_to_registry()

    assert registry.async_get(device.id).sw_version == "1.15.1.1"


async def test_an_unreported_version_does_not_blank_the_card(
    hass: HomeAssistant,
) -> None:
    """Before the first report there is nothing to write, and "" is not an update."""
    coordinator = MammotionRTKCoordinator.__new__(MammotionRTKCoordinator)
    coordinator.hass = hass
    coordinator.unique_name = _NAME
    coordinator.data = RTKBaseStationDevice(name=_NAME)

    registry = dr.async_get(hass)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _NAME)},
        name=_NAME,
        sw_version="1.15.1.1",
    )

    coordinator._sync_firmware_to_registry()

    assert registry.async_get(device.id).sw_version == "1.15.1.1"


async def test_the_update_cycle_actually_performs_the_sync(
    hass: HomeAssistant,
) -> None:
    """The helper is only useful if the poll calls it."""
    coordinator = MammotionRTKCoordinator.__new__(MammotionRTKCoordinator)
    coordinator.hass = hass
    coordinator.unique_name = _NAME
    coordinator.device_name = _NAME
    coordinator.data = RTKBaseStationDevice(name=_NAME)
    coordinator.data.device_version = "1.15.1.1"
    coordinator.manager = MagicMock()
    coordinator.manager.rtk_device.return_value = MagicMock()
    coordinator.manager.mammotion_http = None
    coordinator.manager.reauth_required = None
    coordinator.async_send_command = AsyncMock()
    coordinator.async_send_and_wait = AsyncMock()

    registry = dr.async_get(hass)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _NAME)},
        name=_NAME,
    )

    await coordinator._async_update_data()

    assert registry.async_get(device.id).sw_version == "1.15.1.1"
