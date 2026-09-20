"""The manual user step records the mower picked from the dropdown before the credentials step.

The equivalent check in ``tests/`` could only confirm that the right
expressions appear in ``async_step_user``.  Here the flow is driven end to end,
so a wrong ``step_id``, a dropdown that silently drops a device or an entry
created without its BLE address all fail.
"""

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mammotion.const import (
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_USE_WIFI,
    DOMAIN,
)
from tests_ha.ble_advertisements import inject_advertisement

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


async def _ble_only_entry(hass: HomeAssistant, address: str) -> dict:
    """Pick *address* in the manual flow, which hands the pick straight to the wifi step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    # A created entry is set up straight away, which would try to reach the mower.
    with patch("custom_components.mammotion.async_setup_entry", return_value=True):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: address}
        )


async def test_the_picked_mower_is_recorded_before_the_credentials_step(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """Without this the wifi step sees no BLE device and rejects a BLE-only setup."""
    inject_advertisement(hass, _MOWER, _MOWER_MAC)

    result = await _ble_only_entry(hass, _MOWER_MAC)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == _MOWER
    assert result["data"][CONF_BLE_DEVICES] == {_MOWER: "aa:bb:cc:dd:ee:ff"}
    assert result["data"][CONF_USE_WIFI] is False
    assert result["data"][CONF_HAS_CLOUD_ACCOUNT] is False


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
    """Skipping the dropdown leaves nothing to talk to, so the wifi step must say so."""
    inject_advertisement(hass, _MOWER, _MOWER_MAC)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "wifi"
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
