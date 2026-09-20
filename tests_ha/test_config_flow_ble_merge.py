"""Per-device BLE: discovery merges into any entry, reconfigure re-keys the entry.

The equivalent checks in ``tests/`` parsed ``config_flow.py`` and asserted that
certain names appeared in certain methods.  These drive the real flows against
real config entries and a real device registry, so a wrong abort reason, an
entry that keeps a dead credential blob or a merge that loses a mower all fail.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from pymammotion.transport.base import LoginFailedError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import (
    CONF_ACCOUNT_ID,
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_SESSION_DATA,
    CONF_USE_WIFI,
    DOMAIN,
)
from tests_ha.ble_advertisements import inject_advertisement

_MOWER = "Luba-VS123456"
_MOWER_MAC = "AA:BB:CC:DD:EE:FF"
_OTHER_MOWER = "Yuka-AB654321"
_OTHER_MAC = "11:22:33:44:55:66"
_SPINO = "Spino-E1C36JT4"
_SPINO_MAC = "77:88:99:AA:BB:CC"
_ACCOUNT = "owner@example.com"


def _ble_only_entry(hass: HomeAssistant, **data: object) -> MockConfigEntry:
    """Add an entry that was set up over Bluetooth alone."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=data.pop("unique_id", _MOWER_MAC.lower()),  # type: ignore[arg-type]
        # Bluetooth discovery names the entry after the mower it found.
        title=data.pop("title", _MOWER),  # type: ignore[arg-type]
        data={
            CONF_USE_WIFI: False,
            CONF_HAS_CLOUD_ACCOUNT: False,
            CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC.lower()},
            **data,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _discover(hass: HomeAssistant, name: str, address: str) -> dict:
    """Run the bluetooth discovery flow for *name* without setting anything up."""
    service_info = inject_advertisement(hass, name, address)
    with patch("custom_components.mammotion.async_setup_entry", return_value=True):
        return await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=service_info,
        )


def _cloud_client(account: str = _ACCOUNT) -> MagicMock:
    """Return a MammotionClient stand-in whose login succeeds for *account*."""
    client = MagicMock()
    client.login_and_initiate_cloud = AsyncMock()
    client.stop = AsyncMock()
    client.to_cache.return_value = {CONF_AEP_DATA: {"token": "fresh"}}
    client.mammotion_http.login_info.userInformation.userAccount = account
    return client


async def _reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, user_input: dict, client: MagicMock
) -> dict:
    """Drive the reconfigure step of *entry* with *client* standing in for the cloud."""
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    with (
        patch(
            "custom_components.mammotion.config_flow.MammotionClient",
            return_value=client,
        ),
        patch("custom_components.mammotion.async_setup_entry", return_value=True),
    ):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )


@pytest.mark.usefixtures("enable_bluetooth")
@pytest.mark.parametrize(
    ("name", "address"), [(_OTHER_MOWER, _OTHER_MAC), (_SPINO, _SPINO_MAC)]
)
async def test_a_new_device_joins_the_single_ble_only_entry(
    hass: HomeAssistant, name: str, address: str
) -> None:
    """Discovery no longer needs a cloud account; BLE devices share one entry and one client."""
    entry = _ble_only_entry(hass)

    result = await _discover(hass, name, address)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_BLE_DEVICES] == {
        _MOWER: _MOWER_MAC.lower(),
        name: address.lower(),
    }


@pytest.mark.usefixtures("enable_bluetooth")
async def test_a_device_the_entry_already_lists_by_name_stays_with_it(
    hass: HomeAssistant,
) -> None:
    """The name is matched before anything else, so a cloud entry keeps its own mower."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            CONF_HAS_CLOUD_ACCOUNT: True,
            CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC.lower()},
        },
    )
    entry.add_to_hass(hass)
    _ble_only_entry(hass, unique_id="de:ad:be:ef:00:00")

    result = await _discover(hass, _MOWER, _MOWER_MAC)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_BLE_DEVICES] == {_MOWER: _MOWER_MAC.lower()}


@pytest.mark.usefixtures("enable_bluetooth")
async def test_a_device_already_in_the_registry_gets_its_mac_recorded(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """A cloud-discovered mower has no BLE address until it is seen over the air."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ACCOUNT,
        data={CONF_ACCOUNTNAME: _ACCOUNT, CONF_HAS_CLOUD_ACCOUNT: True},
    )
    entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, _MOWER)}
    )

    result = await _discover(hass, _MOWER, _MOWER_MAC)

    assert result["type"] is FlowResultType.ABORT
    assert (CONNECTION_BLUETOOTH, _MOWER_MAC.lower()) in device_registry.async_get(
        device.id
    ).connections
    assert entry.data[CONF_BLE_DEVICES] == {_MOWER: _MOWER_MAC.lower()}


@pytest.mark.usefixtures("enable_bluetooth")
async def test_an_unknown_device_is_not_forced_into_one_of_several_entries(
    hass: HomeAssistant,
) -> None:
    """With two BLE-only entries there is no single obvious owner, so the user picks."""
    _ble_only_entry(hass)
    _ble_only_entry(hass, unique_id="de:ad:be:ef:00:00", ble_devices={})

    result = await _discover(hass, _OTHER_MOWER, _OTHER_MAC)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"


async def test_adding_an_account_makes_it_the_entry_identity(
    hass: HomeAssistant,
) -> None:
    """A BLE-only entry that gains an account is re-keyed to it, so one client serves it."""
    entry = _ble_only_entry(hass)

    result = await _reconfigure(
        hass,
        entry,
        {CONF_ACCOUNTNAME: _ACCOUNT, CONF_PASSWORD: "hunter2"},
        _cloud_client(),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == _ACCOUNT
    assert entry.data[CONF_ACCOUNT_ID] == _ACCOUNT
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is True
    assert entry.data[CONF_USE_WIFI] is True
    assert entry.data[CONF_AEP_DATA] == {"token": "fresh"}
    assert entry.data[CONF_BLE_DEVICES] == {_MOWER: _MOWER_MAC.lower()}


async def test_adding_an_account_renames_the_entry_to_it(
    hass: HomeAssistant,
) -> None:
    """The entry stands for the account now, so it must not stay named after one mower.

    A BLE-discovered entry is titled with the mower's name.  Reconfiguring it
    with an account re-keyed the unique_id but left the title alone, so the
    account's whole group of devices still showed up under "Luba-...".
    """
    entry = _ble_only_entry(hass)
    assert entry.title == _MOWER

    await _reconfigure(
        hass,
        entry,
        {CONF_ACCOUNTNAME: _ACCOUNT, CONF_PASSWORD: "hunter2"},
        _cloud_client(),
    )

    assert entry.title == _ACCOUNT


async def test_an_account_another_entry_already_holds_absorbs_this_one(
    hass: HomeAssistant,
) -> None:
    """One client per account: the BLE mowers move across and the spare entry goes."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            CONF_HAS_CLOUD_ACCOUNT: True,
            CONF_BLE_DEVICES: {_OTHER_MOWER: _OTHER_MAC.lower()},
        },
    )
    existing.add_to_hass(hass)
    entry = _ble_only_entry(hass)

    result = await _reconfigure(
        hass,
        entry,
        {CONF_ACCOUNTNAME: _ACCOUNT, CONF_PASSWORD: "hunter2"},
        _cloud_client(),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "merged_into_existing_account"
    assert existing.data[CONF_BLE_DEVICES] == {
        _OTHER_MOWER: _OTHER_MAC.lower(),
        _MOWER: _MOWER_MAC.lower(),
    }
    assert hass.config_entries.async_get_entry(entry.entry_id) is None


async def test_a_rejected_login_persists_nothing(hass: HomeAssistant) -> None:
    """Credentials are written on success only — never from the ``finally`` block."""
    entry = _ble_only_entry(hass)
    client = _cloud_client()
    client.login_and_initiate_cloud.side_effect = LoginFailedError(
        "wrong password", "1001"
    )

    result = await _reconfigure(
        hass, entry, {CONF_ACCOUNTNAME: _ACCOUNT, CONF_PASSWORD: "wrong"}, client
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "login_failed"}
    assert entry.unique_id == _MOWER_MAC.lower()
    assert CONF_ACCOUNTNAME not in entry.data
    assert CONF_AEP_DATA not in entry.data
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is False


async def test_removing_the_account_strips_every_cloud_key(
    hass: HomeAssistant,
) -> None:
    """A stale credential blob left behind would be replayed on the next setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            CONF_PASSWORD: "hunter2",
            CONF_ACCOUNT_ID: _ACCOUNT,
            CONF_AEP_DATA: {"token": "stale"},
            CONF_SESSION_DATA: {"session": "stale"},
            CONF_HAS_CLOUD_ACCOUNT: True,
            CONF_USE_WIFI: True,
            CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC.lower()},
        },
    )
    entry.add_to_hass(hass)

    result = await _reconfigure(
        hass, entry, {CONF_ACCOUNTNAME: "", CONF_PASSWORD: ""}, _cloud_client()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    for key in (
        CONF_ACCOUNTNAME,
        CONF_PASSWORD,
        CONF_ACCOUNT_ID,
        CONF_AEP_DATA,
        CONF_SESSION_DATA,
    ):
        assert key not in entry.data
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is False
    assert entry.data[CONF_USE_WIFI] is False
    assert entry.data[CONF_BLE_DEVICES] == {_MOWER: _MOWER_MAC.lower()}
    # The account is gone from the data, so it must not linger in the title.
    assert entry.title == _MOWER


async def test_removing_the_account_of_an_entry_with_no_ble_is_refused(
    hass: HomeAssistant,
) -> None:
    """Dropping the account of a Wi-Fi-only entry would leave nothing to talk to."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            CONF_PASSWORD: "hunter2",
            CONF_HAS_CLOUD_ACCOUNT: True,
        },
    )
    entry.add_to_hass(hass)

    result = await _reconfigure(
        hass, entry, {CONF_ACCOUNTNAME: "", CONF_PASSWORD: ""}, _cloud_client()
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_account_no_ble"}
    assert entry.data[CONF_ACCOUNTNAME] == _ACCOUNT


async def test_a_second_device_joining_reloads_the_entry(
    hass: HomeAssistant,
) -> None:
    """Merging is pointless if the entry is not set up again afterwards.

    A Spino added over Bluetooth, then a mower: the mower landed in
    ``ble_devices`` but ``reload_on_update=False`` meant nothing set it up, so
    the user saw "already configured" and got no mower until a restart.
    """
    entry = _ble_only_entry(hass)
    entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _discover(hass, _OTHER_MOWER, _OTHER_MAC)

    assert _OTHER_MOWER in entry.data[CONF_BLE_DEVICES]
    reload.assert_called_once_with(entry.entry_id)


async def test_a_device_already_listed_does_not_reload(
    hass: HomeAssistant,
) -> None:
    """Nothing changed, so there is nothing to set up again."""
    entry = _ble_only_entry(hass)
    entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _discover(hass, _MOWER, _MOWER_MAC)

    reload.assert_not_called()
