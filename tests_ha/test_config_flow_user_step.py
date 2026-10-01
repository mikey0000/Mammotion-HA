"""The manual user step records the picked mower, then offers the optional login form.

The equivalent check in ``tests/`` could only confirm that the right
expressions appear in ``async_step_user``.  Here the flow is driven end to end,
so a wrong ``step_id``, a dropdown that silently drops a device or an entry
created without its BLE address all fail.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import (
    CONF_ACCOUNT_ID,
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_USE_WIFI,
    DOMAIN,
)
from tests_ha.ble_advertisements import inject_advertisement

_ACCOUNT = "owner@example.com"
_ACCOUNT_ID = "10001"
_MOWER = "Luba-VS123456"
_MOWER_MAC = "AA:BB:CC:DD:EE:FF"
_SPINO = "Spino-E1C36JT4"
_SPINO_MAC = "11:22:33:44:55:66"
_PILE = "SDPX123456"
_PILE_MAC = "77:88:99:AA:BB:CC"


async def _offered_devices(hass: HomeAssistant) -> dict[str, str]:
    """Start the manual flow and return the ``address → name`` dropdown it shows."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["data_schema"].schema[CONF_ADDRESS].container


async def _pick(hass: HomeAssistant, address: str) -> dict:
    """Pick *address* in the manual flow and return what the flow shows next."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: address}
    )


async def _ble_only_entry(hass: HomeAssistant, address: str) -> dict:
    """Pick *address*, then skip the optional login form."""
    result = await _pick(hass, address)
    if result["type"] is not FlowResultType.FORM:
        return result
    assert result["step_id"] == "wifi"
    # A created entry is set up straight away, which would try to reach the mower.
    with patch("custom_components.mammotion.async_setup_entry", return_value=True):
        return await hass.config_entries.flow.async_configure(result["flow_id"], {})


def _login_client() -> MagicMock:
    client = MagicMock()
    client.login_and_initiate_cloud = AsyncMock()
    client.stop = AsyncMock()
    client.mammotion_http.login_info.userInformation.userAccount = _ACCOUNT_ID
    client.to_cache.return_value = {CONF_AEP_DATA: {"token": "fresh"}}
    client.aliyun_device_list = []
    client.mammotion_device_list = []
    return client


async def test_the_picked_mower_is_recorded_before_the_credentials_step(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """The pick leads to the optional login form; skipping it finishes as BLE-only.

    The pick used to be handed to the wifi step as if it were submitted, so the
    login form never appeared and no account could be added in the same flow.
    """
    inject_advertisement(hass, _MOWER, _MOWER_MAC)

    result = await _pick(hass, _MOWER_MAC)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "wifi"
    assert not result["errors"]
    assert all(isinstance(key, vol.Optional) for key in result["data_schema"].schema), (
        "the account must be optional"
    )

    with patch("custom_components.mammotion.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == _MOWER
    assert result["data"][CONF_BLE_DEVICES] == {_MOWER: "aa:bb:cc:dd:ee:ff"}
    assert result["data"][CONF_USE_WIFI] is False
    assert result["data"][CONF_HAS_CLOUD_ACCOUNT] is False


async def test_credentials_after_a_pick_create_the_account_entry_with_the_mower(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """The account entry absorbs the picked mower, so no BLE-only entry is left beside it."""
    inject_advertisement(hass, _MOWER, _MOWER_MAC)
    result = await _pick(hass, _MOWER_MAC)

    with (
        patch(
            "custom_components.mammotion.config_flow.MammotionClient",
            return_value=_login_client(),
        ),
        patch("custom_components.mammotion.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNTNAME: _ACCOUNT, CONF_PASSWORD: "pw"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HAS_CLOUD_ACCOUNT] is True
    assert result["data"][CONF_BLE_DEVICES] == {_MOWER: "aa:bb:cc:dd:ee:ff"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_a_spino_can_be_set_up_from_the_dropdown(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """BLE setup was widened past the mowers, so a pool cleaner must reach an entry too."""
    inject_advertisement(hass, _SPINO, _SPINO_MAC)

    result = await _ble_only_entry(hass, _SPINO_MAC)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BLE_DEVICES] == {_SPINO: "11:22:33:44:55:66"}


async def test_every_supported_kind_is_offered(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """The dropdown is the only way to add a device the manifest never matches."""
    inject_advertisement(hass, _MOWER, _MOWER_MAC)
    inject_advertisement(hass, _SPINO, _SPINO_MAC)

    assert await _offered_devices(hass) == {_MOWER_MAC: _MOWER, _SPINO_MAC: _SPINO}


async def test_the_charging_pile_is_not_offered(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """SDPX is a PC210 dock, not a cleaner, and has none of the state to show."""
    inject_advertisement(hass, _MOWER, _MOWER_MAC)
    inject_advertisement(hass, _PILE, _PILE_MAC)

    assert _PILE_MAC not in await _offered_devices(hass)


async def test_the_entry_is_named_after_the_pick_when_the_ble_object_is_gone(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """A mower that drifts out of range between the dropdown and the wifi step still names its entry."""
    inject_advertisement(hass, _MOWER, _MOWER_MAC)

    with patch(
        "custom_components.mammotion.config_flow.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        result = await _ble_only_entry(hass, _MOWER_MAC)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == _MOWER
    assert result["data"][CONF_BLE_DEVICES] == {_MOWER: "aa:bb:cc:dd:ee:ff"}


async def test_no_pick_and_no_account_is_refused(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """Skipping the dropdown opens the login form clean; only a blank login is refused."""
    inject_advertisement(hass, _MOWER, _MOWER_MAC)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "wifi"
    assert not result["errors"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["errors"] == {"base": "no_account_no_ble"}


@pytest.mark.usefixtures("enable_bluetooth")
async def test_the_wifi_step_is_shown_when_nothing_is_in_range(
    hass: HomeAssistant,
) -> None:
    """With no advertisement to pick from, the flow goes straight to credentials."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "wifi"
    assert not result["errors"]

    with (
        patch(
            "custom_components.mammotion.config_flow.MammotionClient",
            return_value=_login_client(),
        ),
        patch("custom_components.mammotion.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNTNAME: _ACCOUNT, CONF_PASSWORD: "pw"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCOUNT_ID] == _ACCOUNT_ID
    assert CONF_BLE_DEVICES not in result["data"]


@pytest.mark.usefixtures("enable_bluetooth")
@pytest.mark.parametrize(
    ("unique_id", "data"),
    [
        # A BLE-only entry: HA advertises the address uppercase, the entry stores it lowercase.
        (
            "aa:bb:cc:dd:ee:ff",
            {
                CONF_HAS_CLOUD_ACCOUNT: False,
                CONF_BLE_DEVICES: {_MOWER: "aa:bb:cc:dd:ee:ff"},
            },
        ),
        # A cloud entry, keyed by the account, that already holds the mower over BLE.
        (
            "owner@example.com",
            {
                CONF_HAS_CLOUD_ACCOUNT: True,
                CONF_BLE_DEVICES: {_MOWER: "aa:bb:cc:dd:ee:ff"},
            },
        ),
        # A legacy entry keyed by the raw BLE address.
        (_MOWER_MAC, {CONF_HAS_CLOUD_ACCOUNT: True}),
    ],
)
async def test_a_configured_mower_is_not_offered_again(
    hass: HomeAssistant, unique_id: str, data: dict
) -> None:
    """Picking it would add a second entry and a second client for one mower."""
    MockConfigEntry(domain=DOMAIN, unique_id=unique_id, data=data).add_to_hass(hass)
    inject_advertisement(hass, _MOWER, _MOWER_MAC)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["step_id"] == "wifi"


@pytest.mark.usefixtures("enable_bluetooth")
async def test_picking_a_mower_a_cloud_entry_knows_records_it_there(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """The pick goes through the same ownership check as Bluetooth discovery."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="owner@example.com",
        data={CONF_HAS_CLOUD_ACCOUNT: True},
    )
    entry.add_to_hass(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, _MOWER)}
    )
    inject_advertisement(hass, _MOWER, _MOWER_MAC)

    result = await _ble_only_entry(hass, _MOWER_MAC)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_BLE_DEVICES] == {_MOWER: "aa:bb:cc:dd:ee:ff"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
