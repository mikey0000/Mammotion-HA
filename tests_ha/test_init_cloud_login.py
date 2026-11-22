"""How setup answers each outcome of the cloud login.

Setup never logs in with the stored password: it restores the saved session or,
with none, asks the user to sign in.  A network fault is retried by Home
Assistant (``SETUP_RETRY``) and every retry restores the cache again.  A refusal,
a rate limit or any other fault is shown once and never retried, and a dead login
asks for re-authentication.  The exceptions are the library's real ones.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from datetime import timedelta
from unittest.mock import MagicMock

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from cloud_login_support import (
    ACCOUNT,
    CACHED_SESSION,
    login_issue,
    make_cloud_client,
    make_cloud_entry,
    reauth_flows,
    serve_client,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pymammotion.aliyun.exceptions import (
    CloudSetupError,
    LoginException,
    TooManyRequestsException,
)
from pymammotion.http.model.http import (
    AuthorizationCodeError,
    UnauthorizedExceptionError,
)
from pymammotion.transport.base import (
    CachedLoginUnusableError,
    LoginFailedError,
    ReLoginRequiredError,
    TransportRateLimitedError,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from Tea.exceptions import UnretryableException
from Tea.request import TeaRequest

from custom_components.mammotion.const import CONF_AEP_DATA

#: Past Home Assistant's setup-retry delay and the integration's own retry interval.
_LATER = timedelta(hours=1)


def _connection_refused() -> aiohttp.ClientConnectorError:
    key = ConnectionKey("api.example.com", 443, True, True, None, None, None, None)
    return aiohttp.ClientConnectorError(key, OSError(111, "Connection refused"))


_TRANSIENT = [
    pytest.param(lambda: ConnectionError("oauth2/token answered HTTP 503"), id="503"),
    pytest.param(TimeoutError, id="timeout"),
    pytest.param(aiohttp.ServerDisconnectedError, id="server_disconnected"),
    pytest.param(_connection_refused, id="connection_refused"),
    pytest.param(
        lambda: UnretryableException(TeaRequest(), ConnectionResetError("reset")),
        id="tea_wrapped_reset",
    ),
]


@pytest.fixture
def slot() -> Iterator[list[MagicMock]]:
    """Serve every setup the client the test puts in the slot."""
    served: list[MagicMock] = []
    with serve_client(served):
        yield served


@pytest.fixture
async def entries(hass: HomeAssistant) -> AsyncIterator[list[MockConfigEntry]]:
    """Unload what the test loaded, so no timer outlives it."""
    made: list[MockConfigEntry] = []
    yield made
    for entry in made:
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


async def _setup(
    hass: HomeAssistant,
    slot: list[MagicMock],
    entries: list[MockConfigEntry],
    client: MagicMock,
    *,
    cached: bool = True,
    ble: bool = False,
) -> MockConfigEntry:
    slot[:] = [client]
    entry = make_cloud_entry(hass, cached=cached, ble=ble)
    entries.append(entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _later(hass: HomeAssistant) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + _LATER)
    # Home Assistant retries a setup in a background task.
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.regression
async def test_setup_without_a_saved_session_asks_to_sign_in_and_never_logs_in(
    hass: HomeAssistant,
    slot: list[MagicMock],
    entries: list[MockConfigEntry],
) -> None:
    """With no cache, setup sent the stored password on every start."""
    client = make_cloud_client()

    entry = await _setup(hass, slot, entries, client, cached=False)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(reauth_flows(hass)) == 1
    client.login_and_initiate_cloud.assert_not_awaited()
    client.restore_credentials.assert_not_awaited()


@pytest.mark.regression
@pytest.mark.usefixtures("enable_bluetooth")
async def test_setup_without_a_saved_session_keeps_ble_mowers_and_asks_to_sign_in(
    hass: HomeAssistant,
    slot: list[MagicMock],
    entries: list[MockConfigEntry],
) -> None:
    """With no cache and a BLE mower, setup sent the stored password on every start."""
    client = make_cloud_client()

    entry = await _setup(hass, slot, entries, client, cached=False, ble=True)

    assert entry.state is ConfigEntryState.LOADED
    assert len(reauth_flows(hass)) == 1
    client.login_and_initiate_cloud.assert_not_awaited()


@pytest.mark.parametrize("make_exc", _TRANSIENT)
async def test_a_network_fault_at_restore_is_retried_by_home_assistant(
    hass: HomeAssistant,
    slot: list[MagicMock],
    entries: list[MockConfigEntry],
    make_exc: Callable[[], Exception],
) -> None:
    """Only a network fault can recover by waiting, and it costs no password grant."""
    client = make_cloud_client(make_exc())

    entry = await _setup(hass, slot, entries, client)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.data[CONF_AEP_DATA] == CACHED_SESSION
    client.login_and_initiate_cloud.assert_not_awaited()


async def test_home_assistants_retry_restores_the_saved_session_again(
    hass: HomeAssistant,
    slot: list[MagicMock],
    entries: list[MockConfigEntry],
) -> None:
    """The retry spends the cached refresh token, never a password grant."""
    client = make_cloud_client(ConnectionError("HTTP 503"), None)
    entry = await _setup(hass, slot, entries, client)

    await _later(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert client.restore_credentials.await_count == 2
    client.login_and_initiate_cloud.assert_not_awaited()


def _rejected_cache() -> CachedLoginUnusableError:
    return CachedLoginUnusableError(ACCOUNT, "mammotion_data missing")


def _wrong_password() -> LoginFailedError:
    return LoginFailedError(ACCOUNT, "wrong password")


@pytest.mark.parametrize(
    "make_exc",
    [
        pytest.param(_rejected_cache, id="cache_unusable"),
        pytest.param(_wrong_password, id="login_failed"),
    ],
)
async def test_a_refused_saved_login_asks_to_sign_in_and_drops_the_cache(
    hass: HomeAssistant,
    slot: list[MagicMock],
    entries: list[MockConfigEntry],
    make_exc: Callable[[], Exception],
) -> None:
    """Retrying a refused login cannot help; only the user's password can."""
    client = make_cloud_client(make_exc())

    entry = await _setup(hass, slot, entries, client)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(reauth_flows(hass)) == 1
    assert CONF_AEP_DATA not in entry.data
    client.login_and_initiate_cloud.assert_not_awaited()


async def test_a_login_the_server_refused_asks_to_sign_in(
    hass: HomeAssistant,
    slot: list[MagicMock],
    entries: list[MockConfigEntry],
) -> None:
    """``restore_credentials`` sets ``reauth_required`` before raising this."""
    client = make_cloud_client(ReLoginRequiredError(ACCOUNT, "refused"))
    client.reauth_required = "the server no longer accepts the cached login"

    entry = await _setup(hass, slot, entries, client)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(reauth_flows(hass)) == 1


def _transport_refused() -> ReLoginRequiredError:
    return ReLoginRequiredError(ACCOUNT, "Aliyun refreshToken rejected (2401)")


@pytest.mark.regression
@pytest.mark.parametrize(
    "make_exc",
    [
        pytest.param(lambda: CloudSetupError("openId isEmpty"), id="cloud_setup"),
        pytest.param(lambda: TooManyRequestsException("HTTP 429", "iot"), id="429"),
        pytest.param(lambda: TransportRateLimitedError("banned"), id="ban"),
        pytest.param(_transport_refused, id="transport_refused"),
        pytest.param(lambda: UnauthorizedExceptionError("401"), id="unauthorized"),
    ],
)
async def test_a_refusal_or_rate_limit_is_shown_and_never_retried(
    hass: HomeAssistant,
    slot: list[MagicMock],
    entries: list[MockConfigEntry],
    make_exc: Callable[[], Exception],
) -> None:
    """These counted as transient, so a deterministic refusal was re-sent on a timer."""
    client = make_cloud_client(make_exc(), None)
    entry = await _setup(hass, slot, entries, client)

    await _later(hass)

    assert client.restore_credentials.await_count == 1
    assert entry.data[CONF_AEP_DATA] == CACHED_SESSION
    issue = login_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "cloud_login_failed"


@pytest.mark.parametrize(
    "make_exc",
    [
        pytest.param(lambda: LoginException("no session"), id="login_exception"),
        pytest.param(lambda: AuthorizationCodeError("refused"), id="auth_code"),
        pytest.param(lambda: ValueError("malformed payload"), id="fault"),
        pytest.param(
            lambda: UnretryableException(TeaRequest(), ValueError("bad signature")),
            id="tea_fatal",
        ),
    ],
)
async def test_any_other_failure_is_shown_and_never_retried(
    hass: HomeAssistant,
    slot: list[MagicMock],
    entries: list[MockConfigEntry],
    make_exc: Callable[[], Exception],
) -> None:
    """Only a network fault is worth retrying."""
    client = make_cloud_client(make_exc(), None)
    entry = await _setup(hass, slot, entries, client)

    await _later(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert client.restore_credentials.await_count == 1
    issue = login_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "cloud_login_failed"
