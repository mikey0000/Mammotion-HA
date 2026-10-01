"""A cloud login that fails at setup is retried by the integration, boundedly, from the cache.

Setup attempts the login once.  ``ConfigEntryNotReady`` would make Home Assistant
retry setup every 5…600 s for as long as the outage lasts, and on the no-cache path
each retry is an oauth2/token password grant.  Instead the entry loads without the
cloud, shows a repair, and retries every 15 minutes up to five times through
``restore_credentials`` — the cached refresh-token path, never
``login_and_initiate_cloud``.  A retry that succeeds reloads the entry, which is
what builds the cloud devices' coordinators and entities.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pymammotion.client import MammotionClient
from pymammotion.device.handle import DeviceHandle, DeviceRegistry
from pymammotion.transport.base import ReLoginRequiredError, TransportType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

import custom_components.mammotion as mammotion_init
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"
_RETRY_INTERVAL = timedelta(minutes=15)


def _outage() -> ConnectionError:
    return ConnectionError("oauth2/token answered HTTP 503")


def _rejected() -> ReLoginRequiredError:
    return ReLoginRequiredError(_ACCOUNT, "refresh token rejected")


def _client(*restore_results: Exception | None) -> MagicMock:
    """Return a client whose successive restores raise (or, for None, succeed)."""
    client = create_autospec(MammotionClient, instance=True)
    client.restore_credentials.side_effect = list(restore_results)
    client.reauth_required = None
    client.to_cache.return_value = {}
    client.aliyun_device_list = []
    client.mammotion_device_list = []
    client.device_registry = create_autospec(DeviceRegistry, instance=True)
    client.device_registry.all_devices = []
    return client


def _entry(hass: HomeAssistant, *, cached: bool = True) -> MockConfigEntry:
    data: dict[str, object] = {
        CONF_ACCOUNTNAME: _ACCOUNT,
        "password": "hunter2",
        CONF_HAS_CLOUD_ACCOUNT: True,
    }
    if cached:
        data[CONF_AEP_DATA] = {"token": "cached"}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=_ACCOUNT, data=data)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def patched_client() -> Iterator[list[MagicMock]]:
    """Hand every setup (including one a reload starts) the client in the yielded slot."""
    slot: list[MagicMock] = []

    async def _bring_up(*_args: Any, **_kwargs: Any) -> None:
        return None

    with (
        patch.object(
            mammotion_init, "MammotionClient", side_effect=lambda **_: slot[0]
        ),
        patch.object(mammotion_init, "PLATFORMS", []),
        patch.object(mammotion_init, "_async_bring_up_devices", _bring_up),
    ):
        yield slot


@pytest.fixture
async def unload_after(hass: HomeAssistant) -> AsyncIterator[list[MockConfigEntry]]:
    """Unload what the test set up, so no retry timer outlives it."""
    entries: list[MockConfigEntry] = []
    yield entries
    for entry in entries:
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


async def _setup(
    hass: HomeAssistant,
    slot: list[MagicMock],
    client: MagicMock,
    entries: list[MockConfigEntry],
    *,
    cached: bool = True,
) -> MockConfigEntry:
    slot[:] = [client]
    entry = _entry(hass, cached=cached)
    entries.append(entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _advance(hass: HomeAssistant, periods: int = 1) -> None:
    """Move the clock on by *periods* retry intervals, letting each retry finish."""
    for _ in range(periods):
        async_fire_time_changed(hass, dt_util.utcnow() + _RETRY_INTERVAL)
        await hass.async_block_till_done()


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"cloud_login_{entry.entry_id}")


def _reauth_flows(hass: HomeAssistant) -> list[Any]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == "reauth"
    ]


@pytest.mark.regression
async def test_a_transient_setup_failure_loads_the_entry_and_shows_a_repair(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """Setup raised ConfigEntryNotReady, so HA retried it every 5…600 s indefinitely."""
    entry = await _setup(hass, patched_client, _client(_outage()), unload_after)

    assert entry.state is ConfigEntryState.LOADED
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "cloud_login_retrying"
    assert issue.translation_placeholders == {
        "account": _ACCOUNT,
        "error": "oauth2/token answered HTTP 503",
    }


async def test_the_retry_restores_the_cache_and_never_logs_in_with_the_password(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """The retry may only spend the cached refresh token, never a password grant."""
    client = _client(_outage(), _outage())
    await _setup(hass, patched_client, client, unload_after)

    await _advance(hass)

    assert client.restore_credentials.await_count == 2
    client.login_and_initiate_cloud.assert_not_awaited()


async def test_no_retry_runs_before_the_interval(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """Retrying sooner would load the cloud during exactly the outage it reported."""
    client = _client(_outage(), _outage())
    await _setup(hass, patched_client, client, unload_after)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=14))
    await hass.async_block_till_done()

    assert client.restore_credentials.await_count == 1


async def test_retries_stop_after_five_failures(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """The retries are bounded; the repair then says the integration has stopped trying."""
    client = _client(*(_outage() for _ in range(7)))
    entry = await _setup(hass, patched_client, client, unload_after)

    await _advance(hass, periods=7)

    assert client.restore_credentials.await_count == 1 + 5
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "cloud_login_failed"
    assert entry.state is ConfigEntryState.LOADED


async def test_a_successful_retry_reloads_the_entry_to_bring_the_cloud_up(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """The cloud devices' coordinators and entities are built only by setup."""
    client = _client(_outage(), None, None)
    entry = await _setup(hass, patched_client, client, unload_after)

    await _advance(hass)

    # setup, the retry, and the setup the reload ran
    assert client.restore_credentials.await_count == 3
    assert entry.state is ConfigEntryState.LOADED
    assert _issue(hass, entry) is None
    client.login_and_initiate_cloud.assert_not_awaited()


async def test_a_rejection_on_retry_stops_retrying_and_asks_for_reauthentication(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """A rejected refresh token does not become valid by waiting."""
    client = _client()
    outcomes = iter([_outage(), _rejected(), _outage()])

    async def _restore(*_args: Any, **_kwargs: Any) -> None:
        exc = next(outcomes)
        if isinstance(exc, ReLoginRequiredError):
            client.reauth_required = "refresh token rejected"
        raise exc

    client.restore_credentials.side_effect = _restore
    entry = await _setup(hass, patched_client, client, unload_after)

    await _advance(hass, periods=3)

    assert client.restore_credentials.await_count == 2
    assert len(_reauth_flows(hass)) == 1
    assert CONF_AEP_DATA not in entry.data
    assert _issue(hass, entry) is None


async def test_an_unrecoverable_auth_error_cancels_the_pending_retry(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """Once the library has declared the login dead, no retry may spend the cache."""
    client = _client(_outage(), _outage())
    entry = await _setup(hass, patched_client, client, unload_after)

    await client.on_unrecoverable_auth_error(
        _ACCOUNT, TransportType.CLOUD_MAMMOTION, _rejected()
    )
    await hass.async_block_till_done()
    await _advance(hass, periods=2)

    assert client.restore_credentials.await_count == 1
    assert len(_reauth_flows(hass)) == 1
    assert _issue(hass, entry) is None


async def test_an_unrecoverable_auth_error_clears_the_cache_this_client_restored(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """A restart before reauth must not re-spend the dead tokens on setup."""
    client = _client(None)
    entry = await _setup(hass, patched_client, client, unload_after)

    await client.on_unrecoverable_auth_error(
        _ACCOUNT, TransportType.CLOUD_MAMMOTION, _rejected()
    )

    assert CONF_AEP_DATA not in entry.data


@pytest.mark.regression
async def test_an_old_clients_rejection_keeps_the_cache_a_flow_saved_since(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """The old session's rejection wiped the fresh login a reauth had just saved."""
    client = _client(None)
    entry = await _setup(hass, patched_client, client, unload_after)
    fresh = {"token": "saved-by-flow"}
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_AEP_DATA: fresh}
    )

    await client.on_unrecoverable_auth_error(
        _ACCOUNT, TransportType.CLOUD_MAMMOTION, _rejected()
    )

    assert entry.data[CONF_AEP_DATA] is fresh


@pytest.mark.regression
async def test_an_old_clients_rejection_does_not_prompt_after_a_flow_saved_a_login(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """The entry is reloading with the flow's fresh login, so a reauth prompt is spurious."""
    client = _client(None)
    entry = await _setup(hass, patched_client, client, unload_after)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_AEP_DATA: {"token": "saved-by-flow"}}
    )

    await client.on_unrecoverable_auth_error(
        _ACCOUNT, TransportType.CLOUD_MAMMOTION, _rejected()
    )
    await hass.async_block_till_done()

    assert _reauth_flows(hass) == []


async def test_unloading_cancels_the_retry_and_clears_the_repair(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """A timer left behind would restore a session on a client that has been stopped."""
    client = _client(_outage(), _outage())
    entry = await _setup(hass, patched_client, client, unload_after)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await _advance(hass, periods=2)

    assert client.restore_credentials.await_count == 1
    assert _issue(hass, entry) is None


async def test_without_a_cache_there_is_nothing_to_retry_with(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """With no cached session the only retry would be another password grant."""
    client = _client()
    client.login_and_initiate_cloud.side_effect = _outage()
    entry = await _setup(hass, patched_client, client, unload_after, cached=False)

    await _advance(hass, periods=2)

    assert entry.state is ConfigEntryState.LOADED
    client.login_and_initiate_cloud.assert_awaited_once()
    client.restore_credentials.assert_not_awaited()
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "cloud_login_failed"


@pytest.mark.regression
async def test_an_unrecoverable_auth_error_starts_reauth_before_any_ble_connect(
    hass: HomeAssistant,
    patched_client: list[MagicMock],
    unload_after: list[MockConfigEntry],
) -> None:
    """The callback awaited each BLE connect first, so a hung or failing one cost the reauth."""
    client = _client(None)
    handles = []
    for name in ("Luba-VS1", "Luba-VS2"):
        handle = create_autospec(DeviceHandle, instance=True)
        handle.device_name = name
        handle.has_transport.return_value = True
        handles.append(handle)
    client.device_registry.all_devices = handles
    never = asyncio.Event()

    async def _connect(device_name: str, *_args: Any) -> None:
        if device_name == "Luba-VS1":
            raise RuntimeError("proxy dropped the link")
        await never.wait()

    client.connect_ble.side_effect = _connect
    await _setup(hass, patched_client, client, unload_after)

    await asyncio.wait_for(
        client.on_unrecoverable_auth_error(
            _ACCOUNT, TransportType.CLOUD_MAMMOTION, _rejected()
        ),
        timeout=1,
    )
    await asyncio.sleep(0)

    assert len(_reauth_flows(hass)) == 1
    assert [call.args[0] for call in client.connect_ble.await_args_list] == [
        "Luba-VS1",
        "Luba-VS2",
    ]
    for handle in handles:
        handle.set_prefer_ble.assert_called_once_with(value=True)
