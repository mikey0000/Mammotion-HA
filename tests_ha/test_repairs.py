"""The fix flow of the cloud-transport repair.

Fixing first makes one password-free ``retry_transport``.  Only when that is
refused does it ask for the password, and that sign-in is the user's own action:
the fresh session replaces the saved one and the entry reloads from it.
"""

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cloud_login_support import (
    ACCOUNT,
    make_cloud_client,
    make_cloud_entry,
    make_flow_client,
    serve_client,
)
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pymammotion.transport.base import (
    LoginFailedError,
    ReLoginRequiredError,
    TransportType,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import (
    CONF_ACCOUNT_ID,
    CONF_AEP_DATA,
    DOMAIN,
)

_ACCOUNT_ID = "10001"
_FRESH = {"token": "fresh"}


@pytest.fixture
def client() -> Iterator[MagicMock]:
    """Serve the entry's client to its setup and to the reload a fix starts."""
    served = make_cloud_client(None, None)
    with serve_client([served]):
        yield served


@pytest.fixture
async def entry(
    hass: HomeAssistant, client: MagicMock
) -> AsyncIterator[MockConfigEntry]:
    """Load an account entry and give its Aliyun transport up."""
    assert await async_setup_component(hass, "repairs", {})
    loaded = make_cloud_entry(hass)
    hass.config_entries.async_update_entry(
        loaded, data={**loaded.data, CONF_ACCOUNT_ID: _ACCOUNT_ID}
    )
    assert await hass.config_entries.async_setup(loaded.entry_id)
    await hass.async_block_till_done()
    await client.on_transport_availability_changed(
        ACCOUNT, TransportType.CLOUD_ALIYUN, False, "refused"
    )
    yield loaded
    await hass.async_block_till_done()
    if loaded.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(loaded.entry_id)


def _issue_id(entry: MockConfigEntry) -> str:
    return f"cloud_transport_unavailable_{entry.entry_id}_cloud_aliyun"


async def _start(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    result = await repairs_flow_manager(hass).async_init(
        DOMAIN, context={"issue_id": _issue_id(entry)}
    )
    assert result["step_id"] == "retry"
    return result


async def _submit(
    hass: HomeAssistant, result: dict[str, Any], user_input: dict[str, Any]
) -> dict[str, Any]:
    return await repairs_flow_manager(hass).async_configure(
        result["flow_id"], user_input
    )


async def _password_step(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> dict[str, Any]:
    client.retry_transport.side_effect = ReLoginRequiredError(ACCOUNT, "refused")
    result = await _submit(hass, await _start(hass, entry), {})
    assert result["step_id"] == "password"
    return result


async def _sign_in(
    hass: HomeAssistant, result: dict[str, Any], flow_client: MagicMock
) -> dict[str, Any]:
    with patch(
        "custom_components.mammotion.config_flow.MammotionClient",
        return_value=flow_client,
    ):
        return await _submit(hass, result, {CONF_PASSWORD: "new-password"})


async def test_the_fix_retries_the_transport_without_a_password(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """A retry from the healthy login may cure it; the password is the last resort."""
    result = await _submit(hass, await _start(hass, entry), {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    client.retry_transport.assert_awaited_once_with(ACCOUNT, TransportType.CLOUD_ALIYUN)
    client.login_and_initiate_cloud.assert_not_awaited()
    assert ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(entry)) is None


async def test_a_successful_retry_reloads_so_every_mower_gets_its_entities(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """Only setup builds coordinators, and a mower the restore could not list has none."""
    await _submit(hass, await _start(hass, entry), {})
    await hass.async_block_till_done()

    assert client.restore_credentials.await_count == 2
    assert entry.state is ConfigEntryState.LOADED


async def test_the_retry_step_shows_the_issues_details(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The step names the account, the service and the dark mowers, as the repair does."""
    result = await _start(hass, entry)

    assert result["description_placeholders"] == {
        "account": ACCOUNT,
        "service": "Aliyun IoT",
        "mowers": "-",
    }


async def test_a_network_fault_on_retry_offers_to_try_again(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """A blip says nothing about the login, so it is no reason to ask for the password."""
    client.retry_transport.side_effect = ConnectionError("HTTP 503")

    result = await _submit(hass, await _start(hass, entry), {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "retry"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_refused_retry_asks_for_the_password(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """A refusal from the healthy login leaves a sign-in as the only cure."""
    result = await _password_step(hass, entry, client)

    assert result["type"] is FlowResultType.FORM
    client.login_and_initiate_cloud.assert_not_awaited()


async def test_signing_in_saves_the_fresh_session_and_reloads_from_it(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """The reload must restore the sign-in's session, not log in a second time."""
    flow_client = make_flow_client(_ACCOUNT_ID, {CONF_AEP_DATA: _FRESH})
    result = await _sign_in(
        hass, await _password_step(hass, entry, client), flow_client
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_AEP_DATA] == _FRESH
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert client.restore_credentials.await_args.args[2][CONF_AEP_DATA] == _FRESH
    client.login_and_initiate_cloud.assert_not_awaited()
    flow_client.stop.assert_awaited_once()


async def test_a_refused_password_is_shown_on_the_password_step(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """Nothing is saved, and the user can correct the password."""
    flow_client = make_flow_client(_ACCOUNT_ID, {})
    flow_client.login_and_initiate_cloud.side_effect = LoginFailedError(
        ACCOUNT, "wrong password"
    )

    result = await _sign_in(
        hass, await _password_step(hass, entry, client), flow_client
    )

    assert result["step_id"] == "password"
    assert result["errors"] == {"base": "login_failed"}
    assert entry.data[CONF_PASSWORD] == "hunter2"


async def test_signing_in_as_another_account_is_refused(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """Saving another account's session would hand this entry someone else's mowers."""
    flow_client = make_flow_client("99999", {CONF_AEP_DATA: _FRESH})

    result = await _sign_in(
        hass, await _password_step(hass, entry, client), flow_client
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_AEP_DATA] != _FRESH
