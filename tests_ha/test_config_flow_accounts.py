"""Cloud-account handling shared by the wifi, reauth and reconfigure steps.

A Mammotion account has one live login session: a fresh password login
replaces it server-side.  So a flow must recognise an account that is already
configured *before* it logs in, map every login failure the same way in all
three steps, and stop its throwaway client however the step ends.
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pymammotion.aliyun.exceptions import CloudSetupError, TooManyRequestsException
from pymammotion.transport.base import LoginFailedError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.const import (
    CONF_ACCOUNT_ID,
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_USE_WIFI,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"
_ACCOUNT_ID = "10001"
_MOWER = "Luba-VS123456"
_MOWER_MAC = "aa:bb:cc:dd:ee:ff"
_OTHER_MOWER = "Yuka-AB654321"
_OTHER_MAC = "11:22:33:44:55:66"
_REMOVE = "remove_account"


def _client(
    account_id: str = _ACCOUNT_ID, device_names: tuple[str, ...] = ()
) -> MagicMock:
    """Return a stand-in for the flow's throwaway client whose login succeeds."""
    client = MagicMock()
    client.login_and_initiate_cloud = AsyncMock()
    client.stop = AsyncMock()
    client.mammotion_http.login_info.userInformation.userAccount = account_id
    client.to_cache.return_value = {CONF_AEP_DATA: {"token": "fresh"}}
    client.aliyun_device_list = [_create_ble_only_device(n) for n in device_names]
    client.mammotion_device_list = []
    return client


def _cloud_entry(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    unique_id = overrides.pop("unique_id", _ACCOUNT_ID)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        title=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            CONF_PASSWORD: "hunter2",
            CONF_ACCOUNT_ID: _ACCOUNT_ID,
            CONF_HAS_CLOUD_ACCOUNT: True,
            CONF_USE_WIFI: True,
            CONF_AEP_DATA: {"token": "live"},
            **overrides,
        },
    )
    entry.add_to_hass(hass)
    return entry


def _ble_only_entry(
    hass: HomeAssistant, ble_devices: dict[str, str], unique_id: str = _MOWER_MAC
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        title=next(iter(ble_devices)),
        data={
            CONF_USE_WIFI: False,
            CONF_HAS_CLOUD_ACCOUNT: False,
            CONF_BLE_DEVICES: ble_devices,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _add_account(
    hass: HomeAssistant, client: MagicMock, account: str = _ACCOUNT
) -> dict[str, Any]:
    """Drive Add Integration with nothing in BLE range, straight to the credentials."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "wifi"
    with (
        patch(
            "custom_components.mammotion.config_flow.MammotionClient",
            return_value=client,
        ),
        patch("custom_components.mammotion.async_setup_entry", return_value=True),
    ):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNTNAME: account, CONF_PASSWORD: "pw"}
        )


async def _reauth(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> dict[str, Any]:
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with (
        patch(
            "custom_components.mammotion.config_flow.MammotionClient",
            return_value=client,
        ),
        patch("custom_components.mammotion.async_setup_entry", return_value=True),
    ):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )


async def _reconfigure(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    user_input: dict[str, Any],
    client: MagicMock,
) -> dict[str, Any]:
    result = await entry.start_reconfigure_flow(hass)
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
@pytest.mark.parametrize("typed", [_ACCOUNT, " Owner@Example.com ", _ACCOUNT_ID])
async def test_re_adding_a_configured_account_aborts_before_logging_in(
    hass: HomeAssistant, typed: str
) -> None:
    """The login would replace the live entry's session and connect with its clientId."""
    # A legacy entry keyed by a BLE address must be recognised by its data, not its unique_id.
    _cloud_entry(hass, unique_id="AA:BB:CC:DD:EE:FF")
    client = _client()

    result = await _add_account(hass, client, typed)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    client.login_and_initiate_cloud.assert_not_awaited()


@pytest.mark.usefixtures("enable_bluetooth")
async def test_an_alias_of_a_configured_account_hands_it_the_fresh_login(
    hass: HomeAssistant,
) -> None:
    """Typed differently, the account is only known after the login, which already replaced its session."""
    entry = _cloud_entry(hass, **{CONF_ACCOUNTNAME: "+64 21 000 000"})
    client = _client()

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await _add_account(hass, client)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_AEP_DATA] == {"token": "fresh"}
    reload.assert_called_once_with(entry.entry_id)
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("enable_bluetooth")
async def test_a_new_account_absorbs_the_ble_only_entry_of_its_mower(
    hass: HomeAssistant,
) -> None:
    """Otherwise two entries, and two clients, claim the same mower."""
    ble_only = _ble_only_entry(hass, {_MOWER: _MOWER_MAC})

    result = await _add_account(hass, _client(device_names=(_MOWER,)))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BLE_DEVICES] == {_MOWER: _MOWER_MAC}
    assert hass.config_entries.async_get_entry(ble_only.entry_id) is None


@pytest.mark.usefixtures("enable_bluetooth")
async def test_a_ble_only_entry_keeps_the_mowers_the_account_does_not_own(
    hass: HomeAssistant,
) -> None:
    """Only the account's own mowers move; a neighbour's stays where it was."""
    ble_only = _ble_only_entry(hass, {_MOWER: _MOWER_MAC, _OTHER_MOWER: _OTHER_MAC})

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await _add_account(hass, _client(device_names=(_MOWER,)))

    assert result["data"][CONF_BLE_DEVICES] == {_MOWER: _MOWER_MAC}
    assert ble_only.data[CONF_BLE_DEVICES] == {_OTHER_MOWER: _OTHER_MAC}
    reload.assert_called_once_with(ble_only.entry_id)


#: (raised by the login, form error shown, whether a traceback is logged)
_LOGIN_FAILURES = [
    pytest.param(
        (LoginFailedError("wrong password", "1001"), "login_failed", False),
        id="wrong-password",
    ),
    pytest.param((CloudSetupError("no AEP"), "cannot_connect", False), id="aliyun"),
    pytest.param((RuntimeError("boom"), "cannot_connect", True), id="unexpected"),
]


@pytest.mark.usefixtures("enable_bluetooth")
@pytest.mark.parametrize("failure", _LOGIN_FAILURES)
async def test_the_wifi_step_maps_login_failures(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    failure: tuple[Exception, str, bool],
) -> None:
    """A wrong password is the user's mistake, not a connection fault with a traceback."""
    error, expected, logged_traceback = failure
    client = _client()
    client.login_and_initiate_cloud.side_effect = error

    with caplog.at_level(logging.WARNING):
        result = await _add_account(hass, client)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert any(r.exc_info for r in caplog.records) is logged_traceback
    client.stop.assert_awaited_once()


@pytest.mark.parametrize("failure", _LOGIN_FAILURES)
@pytest.mark.parametrize("step", ["reauth", "reconfigure"])
async def test_reauth_and_reconfigure_map_login_failures_like_the_wifi_step(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    step: str,
    failure: tuple[Exception, str, bool],
) -> None:
    """One mapping for all three steps, so a failure reads the same wherever it happens."""
    error, expected, logged_traceback = failure
    entry = _cloud_entry(hass)
    client = _client()
    client.login_and_initiate_cloud.side_effect = error

    with caplog.at_level(logging.WARNING):
        if step == "reauth":
            result = await _reauth(hass, entry, client)
        else:
            result = await _reconfigure(
                hass, entry, {CONF_ACCOUNTNAME: _ACCOUNT, CONF_PASSWORD: "pw"}, client
            )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert any(r.exc_info for r in caplog.records) is logged_traceback
    assert entry.data[CONF_AEP_DATA] == {"token": "live"}
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("enable_bluetooth")
async def test_a_rate_limited_login_aborts_and_stops_the_client(
    hass: HomeAssistant,
) -> None:
    """An abort leaves the step early; the client must not be left running."""
    client = _client()
    client.login_and_initiate_cloud.side_effect = TooManyRequestsException(
        "429", "slow down"
    )

    result = await _add_account(hass, client)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "api_limit_exceeded"
    client.stop.assert_awaited_once()


@pytest.mark.parametrize("legacy_unique_id", ["AA:BB:CC:DD:EE:FF", _MOWER])
async def test_a_legacy_entry_can_complete_reauth(
    hass: HomeAssistant, legacy_unique_id: str
) -> None:
    """Entries once keyed by BLE address or device name never matched the account and were stuck."""
    entry = _cloud_entry(hass, unique_id=legacy_unique_id)

    result = await _reauth(hass, entry, _client())

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_AEP_DATA] == {"token": "fresh"}
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.unique_id == _ACCOUNT_ID


async def test_reauth_with_another_accounts_credentials_is_refused(
    hass: HomeAssistant,
) -> None:
    """The entry's devices belong to its own account; another one's session would orphan them."""
    entry = _cloud_entry(hass)

    result = await _reauth(hass, entry, _client(account_id="99999"))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_AEP_DATA] == {"token": "live"}


async def test_reauth_is_not_blocked_by_another_flow_for_the_account(
    hass: HomeAssistant,
) -> None:
    """The password is already spent by then, so a late already_in_progress abort wastes the login."""
    entry = _cloud_entry(hass)
    other = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    flow = hass.config_entries.flow._progress[other["flow_id"]]
    await flow.async_set_unique_id(_ACCOUNT_ID)

    result = await _reauth(hass, entry, _client())

    assert result["reason"] == "reauth_successful"


async def test_the_reconfigure_form_never_sends_the_stored_password(
    hass: HomeAssistant,
) -> None:
    """A schema default is rendered into the browser; the password must not be."""
    entry = _cloud_entry(hass)

    result = await entry.start_reconfigure_flow(hass)

    schema = result["data_schema"].schema
    fields = {str(key): key for key in schema}
    assert fields[CONF_PASSWORD].default is vol.UNDEFINED
    assert fields[CONF_ACCOUNTNAME].default is vol.UNDEFINED
    assert fields[CONF_ACCOUNTNAME].description == {"suggested_value": _ACCOUNT}
    assert _REMOVE in fields


async def test_the_account_is_removed_from_what_the_frontend_submits(
    hass: HomeAssistant,
) -> None:
    """The frontend drops cleared optional fields, so clearing both used to submit nothing at all."""
    entry = _cloud_entry(hass, **{CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC}})
    client = _client()

    result = await _reconfigure(hass, entry, {_REMOVE: True}, client)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is False
    assert CONF_ACCOUNTNAME not in entry.data
    assert CONF_AEP_DATA not in entry.data
    client.login_and_initiate_cloud.assert_not_awaited()


async def test_a_reconfigure_without_a_password_asks_for_one(
    hass: HomeAssistant,
) -> None:
    """With no stored password prefilled, submitting the suggested account alone must not log in."""
    entry = _cloud_entry(hass)
    client = _client()

    result = await _reconfigure(
        hass, entry, {CONF_ACCOUNTNAME: _ACCOUNT, _REMOVE: False}, client
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "credentials_required"}
    client.login_and_initiate_cloud.assert_not_awaited()
    assert entry.data[CONF_ACCOUNTNAME] == _ACCOUNT


async def test_reconfiguring_into_a_configured_account_merges_without_logging_in(
    hass: HomeAssistant,
) -> None:
    """The other entry's live session would be replaced by a login it does not need."""
    existing = _cloud_entry(hass)
    entry = _ble_only_entry(hass, {_MOWER: _MOWER_MAC})
    client = _client()

    with patch.object(hass.config_entries, "async_schedule_reload"):
        result = await _reconfigure(
            hass, entry, {CONF_ACCOUNTNAME: _ACCOUNT, CONF_PASSWORD: "pw"}, client
        )

    assert result["reason"] == "merged_into_existing_account"
    assert existing.data[CONF_BLE_DEVICES] == {_MOWER: _MOWER_MAC}
    assert existing.data[CONF_AEP_DATA] == {"token": "live"}
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    client.login_and_initiate_cloud.assert_not_awaited()
