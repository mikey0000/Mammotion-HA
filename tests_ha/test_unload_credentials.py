"""A loaded entry's client never writes its credential cache over one a flow just saved.

Reauth and reconfigure save a fresh login into the entry and then reload it, so
the unload runs with the *old* client.  That client's session is either another
account's or one the fresh login has already replaced server-side; writing it
back restores the wrong account, or a dead session, or cloud keys into an entry
that no longer has an account.  These run the real setup/unload lifecycle with
only the login and the device bring-up held back.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pymammotion.data.model.device import MowingDevice
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

_MOWER = "Luba-VS123456"
_MOWER_MAC = "aa:bb:cc:dd:ee:ff"
_ACCOUNT = "owner@example.com"
_ACCOUNT_ID = "10001"
_OTHER_ACCOUNT = "spouse@example.com"
_OTHER_ACCOUNT_ID = "20002"


def _setup_client(account_id: str, cache: dict[str, Any]) -> MagicMock:
    """Return the client ``async_setup_entry`` builds, logged in to *account_id*."""
    devices: dict[str, Any] = {}
    handles: dict[str, MagicMock] = {}

    def _handle(name: str) -> MagicMock:
        if name not in handles:
            handle = MagicMock(device_name=name)
            handle.stop = AsyncMock()
            handles[name] = handle
        return handles[name]

    async def _add_ble_only_device(
        *, device_name: str, initial_device: MowingDevice, **_kwargs: Any
    ) -> None:
        devices.setdefault(device_name, initial_device)

    client = MagicMock()
    client.mower = MagicMock(side_effect=_handle)
    client.get_device_by_name = MagicMock(side_effect=devices.get)
    client.add_ble_only_device = AsyncMock(side_effect=_add_ble_only_device)
    client.remove_device = AsyncMock()
    client.stop = AsyncMock()
    client.aliyun_device_list = []
    client.mammotion_device_list = []
    client.mammotion_http.login_info.userInformation.userAccount = account_id
    client.to_cache = MagicMock(return_value=cache)
    return client


def _flow_client(account_id: str, cache: dict[str, Any]) -> MagicMock:
    """Return the throwaway client a config flow logs in with."""
    client = MagicMock()
    client.login_and_initiate_cloud = AsyncMock()
    client.stop = AsyncMock()
    client.aliyun_device_list = []
    client.mammotion_device_list = []
    client.mammotion_http.login_info.userInformation.userAccount = account_id
    client.to_cache.return_value = cache
    return client


def _cloud_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ACCOUNT_ID,
        title=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            CONF_PASSWORD: "hunter2",
            CONF_ACCOUNT_ID: _ACCOUNT_ID,
            CONF_HAS_CLOUD_ACCOUNT: True,
            CONF_USE_WIFI: True,
            CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC},
            CONF_AEP_DATA: {"token": "restored"},
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def setup_clients() -> list[MagicMock]:
    """Clients handed to successive ``async_setup_entry`` runs, in order."""
    return []


@pytest.fixture
def held_back(setup_clients: list[MagicMock]) -> Iterator[None]:
    """Patch out the login, the platforms and the device bring-up for every setup run."""

    async def _attempt_login(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def _bring_up(*_args: Any, **_kwargs: Any) -> None:
        return None

    with (
        patch(
            "custom_components.mammotion.MammotionClient",
            side_effect=lambda **_kwargs: setup_clients.pop(0),
        ),
        patch("custom_components.mammotion.PLATFORMS", []),
        patch("custom_components.mammotion._async_attempt_login", _attempt_login),
        patch("custom_components.mammotion._async_bring_up_devices", _bring_up),
    ):
        yield


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _reconfigure(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    user_input: dict[str, Any],
    client: MagicMock,
) -> dict[str, Any]:
    result = await entry.start_reconfigure_flow(hass)
    with patch(
        "custom_components.mammotion.config_flow.MammotionClient", return_value=client
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
    # The flow saves first and reloads afterwards; the reload is what unloads.
    await hass.async_block_till_done()
    return result


@pytest.mark.usefixtures("held_back")
async def test_switching_account_keeps_the_new_accounts_session(
    hass: HomeAssistant, setup_clients: list[MagicMock]
) -> None:
    """``MammotionHTTP.from_cache`` never checks the account, so a write-back would restore the old one."""
    entry = _cloud_entry(hass)
    old = _setup_client(_ACCOUNT_ID, {CONF_AEP_DATA: {"token": "old-account"}})
    # The reloaded client's session is rejected, so only the unload could write.
    setup_clients.extend([old, _setup_client(_OTHER_ACCOUNT_ID, {})])
    await _set_up(hass, entry)

    result = await _reconfigure(
        hass,
        entry,
        {CONF_ACCOUNTNAME: _OTHER_ACCOUNT, CONF_PASSWORD: "pw2"},
        _flow_client(_OTHER_ACCOUNT_ID, {CONF_AEP_DATA: {"token": "new-account"}}),
    )

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ACCOUNT_ID] == _OTHER_ACCOUNT_ID
    assert entry.data[CONF_AEP_DATA] == {"token": "new-account"}


@pytest.mark.usefixtures("held_back")
async def test_reconfiguring_the_same_account_keeps_the_fresh_login(
    hass: HomeAssistant, setup_clients: list[MagicMock]
) -> None:
    """The flow's login replaced the old session server-side; writing it back saves a dead cache."""
    entry = _cloud_entry(hass)
    old = _setup_client(_ACCOUNT_ID, {CONF_AEP_DATA: {"token": "replaced"}})
    setup_clients.extend([old, _setup_client(_ACCOUNT_ID, {})])
    await _set_up(hass, entry)

    await _reconfigure(
        hass,
        entry,
        {CONF_ACCOUNTNAME: _ACCOUNT, CONF_PASSWORD: "hunter2"},
        _flow_client(_ACCOUNT_ID, {CONF_AEP_DATA: {"token": "fresh"}}),
    )

    assert entry.data[CONF_AEP_DATA] == {"token": "fresh"}


@pytest.mark.usefixtures("held_back")
async def test_removing_the_account_leaves_no_cloud_keys_after_the_reload(
    hass: HomeAssistant, setup_clients: list[MagicMock]
) -> None:
    """A BLE-only entry holding a credential blob would replay it on the next setup."""
    entry = _cloud_entry(hass)
    old = _setup_client(_ACCOUNT_ID, {CONF_AEP_DATA: {"token": "old"}})
    setup_clients.extend([old, _setup_client(_ACCOUNT_ID, {})])
    await _set_up(hass, entry)

    result = await _reconfigure(
        hass, entry, {"remove_account": True}, _flow_client(_ACCOUNT_ID, {})
    )

    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is False
    assert CONF_AEP_DATA not in entry.data
    assert CONF_ACCOUNTNAME not in entry.data


@pytest.mark.usefixtures("held_back")
async def test_a_refresh_after_a_flow_saved_does_not_overwrite_it(
    hass: HomeAssistant, setup_clients: list[MagicMock]
) -> None:
    """The old client keeps running until the reload, and its refresh callback can still fire."""
    entry = _cloud_entry(hass)
    old = _setup_client(_ACCOUNT_ID, {CONF_AEP_DATA: {"token": "old"}})
    setup_clients.append(old)
    await _set_up(hass, entry)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_AEP_DATA: {"token": "saved-by-flow"}}
    )

    await old.on_credentials_updated()

    assert entry.data[CONF_AEP_DATA] == {"token": "saved-by-flow"}
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("held_back")
async def test_every_rotation_is_persisted_without_an_unload(
    hass: HomeAssistant, setup_clients: list[MagicMock]
) -> None:
    """The refresh callback is what saves rotations, so unload has nothing left to write."""
    entry = _cloud_entry(hass)
    client = _setup_client(_ACCOUNT_ID, {CONF_AEP_DATA: {"token": "first"}})
    setup_clients.append(client)
    await _set_up(hass, entry)

    for token in ("second", "third"):
        client.to_cache.return_value = {CONF_AEP_DATA: {"token": token}}
        await client.on_credentials_updated()
        assert entry.data[CONF_AEP_DATA] == {"token": token}

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.data[CONF_AEP_DATA] == {"token": "third"}


@pytest.mark.usefixtures("held_back")
async def test_a_client_of_another_account_never_writes(
    hass: HomeAssistant, setup_clients: list[MagicMock]
) -> None:
    """The cache is keyed by nothing but the entry, so the account is the only guard."""
    entry = _cloud_entry(hass)
    client = _setup_client(_OTHER_ACCOUNT_ID, {CONF_AEP_DATA: {"token": "foreign"}})
    setup_clients.append(client)

    await _set_up(hass, entry)

    assert entry.data[CONF_AEP_DATA] == {"token": "restored"}
    await hass.config_entries.async_unload(entry.entry_id)
