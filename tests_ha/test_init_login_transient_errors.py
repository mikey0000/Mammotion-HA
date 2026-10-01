"""A setup login is attempted once; a failure loads the entry without the cloud.

pymammotion raises ``ConnectionError`` for HTTP 408/429/5xx from ``oauth2/token``
and lets timeouts and aiohttp connection errors through as their own types.  None
of them may become ``ConfigEntryNotReady``: Home Assistant would retry setup every
5…600 s for as long as the outage lasts, and without a cache each retry is a
password grant.  Nor may a rejected cache be answered with a second password login
here: ``restore_credentials`` already makes the one sanctioned fallback itself.
The exceptions are the real ones.
"""

from collections.abc import Callable, Iterator
from unittest.mock import MagicMock, create_autospec

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from pymammotion.aliyun.exceptions import TooManyRequestsException
from pymammotion.client import MammotionClient
from pymammotion.transport.base import (
    LoginFailedError,
    ReLoginRequiredError,
    TransportRateLimitedError,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from Tea.exceptions import UnretryableException
from Tea.request import TeaRequest

from custom_components.mammotion import _async_attempt_login, _CloudLogin
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"


def _connection_refused() -> aiohttp.ClientConnectorError:
    key = ConnectionKey("api.example.com", 443, True, True, None, None, None, None)
    return aiohttp.ClientConnectorError(key, OSError(111, "Connection refused"))


def _tea_wrapped_reset() -> UnretryableException:
    """Tea keeps the transient cause in ``inner_exception``, not ``__cause__``."""
    return UnretryableException(TeaRequest(), ConnectionResetError("reset by peer"))


_TRANSIENT: list[Callable[[], Exception]] = [
    lambda: ConnectionError("oauth2/token answered HTTP 503"),
    TimeoutError,
    aiohttp.ConnectionTimeoutError,
    aiohttp.ServerDisconnectedError,
    _connection_refused,
    _tea_wrapped_reset,
    lambda: TooManyRequestsException("HTTP 429", "iot-id"),
    lambda: TransportRateLimitedError("cloud send quota exhausted"),
]
_TRANSIENT_IDS = [
    "http_503",
    "timeout",
    "connection_timeout",
    "server_disconnected",
    "connection_refused",
    "tea_wrapped_reset",
    "aliyun_429",
    "transport_rate_limited",
]
transient = pytest.mark.parametrize("make_exc", _TRANSIENT, ids=_TRANSIENT_IDS)


def _entry(hass: HomeAssistant, *, cached: bool) -> MockConfigEntry:
    data: dict[str, object] = {CONF_ACCOUNTNAME: _ACCOUNT, CONF_HAS_CLOUD_ACCOUNT: True}
    if cached:
        data[CONF_AEP_DATA] = {"token": "cached"}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=_ACCOUNT, data=data)
    entry.add_to_hass(hass)
    return entry


def _client(
    *, restore: Exception | None = None, login: Exception | None = None
) -> MagicMock:
    client = create_autospec(MammotionClient, instance=True)
    client.restore_credentials.side_effect = restore
    client.login_and_initiate_cloud.side_effect = login
    client.reauth_required = None
    return client


def _rejected_session(client: MagicMock) -> ReLoginRequiredError:
    """Return what a restore raises once the library has declared the login dead."""
    client.reauth_required = "refresh token rejected"
    return ReLoginRequiredError(_ACCOUNT, "refresh token rejected")


def _transport_scoped() -> ReLoginRequiredError:
    """Return an unrenewable transport on a healthy login (``reauth_required`` stays None)."""
    return ReLoginRequiredError(_ACCOUNT, "Aliyun refreshToken rejected (2401)")


@pytest.fixture
def logins() -> Iterator[list[_CloudLogin]]:
    """Cancel every retry an attempt scheduled, so no timer outlives the test."""
    made: list[_CloudLogin] = []
    yield made
    for login in made:
        login.async_cancel()


def _issue_key(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"cloud_login_{entry.entry_id}")
    return None if issue is None else issue.translation_key


async def _attempt(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: MagicMock,
    logins: list[_CloudLogin],
    *,
    ble_fallback: bool,
) -> bool:
    login = _CloudLogin(hass, entry, client, _ACCOUNT, "password")
    logins.append(login)
    return await _async_attempt_login(login, ble_fallback=ble_fallback)


ble_fallback = pytest.mark.parametrize("ble", [False, True], ids=["cloud_only", "ble"])


@pytest.mark.regression
@ble_fallback
@transient
async def test_a_transient_login_failure_loads_without_the_cloud(
    hass: HomeAssistant,
    logins: list[_CloudLogin],
    make_exc: Callable[[], Exception],
    ble: bool,
) -> None:
    """It raised ConfigEntryNotReady, so HA re-ran the password grant every 5…600 s."""
    entry = _entry(hass, cached=False)
    client = _client(login=make_exc())

    assert await _attempt(hass, entry, client, logins, ble_fallback=ble) is False

    client.login_and_initiate_cloud.assert_awaited_once()
    # Nothing cached to retry from, so the repair asks for a reload instead.
    assert _issue_key(hass, entry) == "cloud_login_failed"


@pytest.mark.regression
@ble_fallback
@transient
async def test_a_transient_restore_failure_keeps_the_cache_and_retries(
    hass: HomeAssistant,
    logins: list[_CloudLogin],
    make_exc: Callable[[], Exception],
    ble: bool,
) -> None:
    """The server never judged the cache, so the retry must still have it to use."""
    entry = _entry(hass, cached=True)
    client = _client(restore=make_exc())

    assert await _attempt(hass, entry, client, logins, ble_fallback=ble) is False

    assert entry.data[CONF_AEP_DATA] == {"token": "cached"}
    client.login_and_initiate_cloud.assert_not_awaited()
    assert _issue_key(hass, entry) == "cloud_login_retrying"


@pytest.mark.regression
@ble_fallback
async def test_a_transport_scoped_auth_error_keeps_the_cache_and_skips_the_password(
    hass: HomeAssistant, logins: list[_CloudLogin], ble: bool
) -> None:
    """Every AuthError wiped the cache and ran a second password login."""
    entry = _entry(hass, cached=True)
    client = _client(restore=_transport_scoped())

    assert await _attempt(hass, entry, client, logins, ble_fallback=ble) is False

    client.login_and_initiate_cloud.assert_not_awaited()
    assert entry.data[CONF_AEP_DATA] == {"token": "cached"}
    assert _issue_key(hass, entry) == "cloud_login_retrying"


@pytest.mark.regression
async def test_a_dead_login_without_ble_asks_for_reauthentication_once(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """The rejection was answered with a second password login before the reauth."""
    entry = _entry(hass, cached=True)
    client = _client()
    client.restore_credentials.side_effect = _rejected_session(client)

    with pytest.raises(ConfigEntryAuthFailed):
        await _attempt(hass, entry, client, logins, ble_fallback=False)

    client.login_and_initiate_cloud.assert_not_awaited()
    assert CONF_AEP_DATA not in entry.data


async def test_a_rejected_password_without_ble_asks_for_reauthentication(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """``restore_credentials`` raises this after its own fallback login was refused."""
    entry = _entry(hass, cached=True)
    client = _client(restore=LoginFailedError(_ACCOUNT, "wrong password"))

    with pytest.raises(ConfigEntryAuthFailed):
        await _attempt(hass, entry, client, logins, ble_fallback=False)

    assert CONF_AEP_DATA not in entry.data
    assert _issue_key(hass, entry) is None


@ble_fallback
@pytest.mark.parametrize("cached", [False, True], ids=["login", "restore"])
async def test_an_unexpected_login_failure_loads_without_the_cloud_and_is_not_retried(
    hass: HomeAssistant, logins: list[_CloudLogin], cached: bool, ble: bool
) -> None:
    """Only a network failure is worth retrying; a fault is shown and left to a reload."""
    entry = _entry(hass, cached=cached)
    exc = ValueError("malformed login payload")
    client = _client(restore=exc, login=exc)

    assert await _attempt(hass, entry, client, logins, ble_fallback=ble) is False

    assert _issue_key(hass, entry) == "cloud_login_failed"


async def test_a_tea_error_with_a_fatal_inner_cause_is_not_retried(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """The wrapper is classified by what it wraps, both ways round."""
    entry = _entry(hass, cached=True)
    exc = UnretryableException(TeaRequest(), ValueError("bad signature"))

    assert (
        await _attempt(hass, entry, _client(restore=exc), logins, ble_fallback=False)
        is False
    )

    assert _issue_key(hass, entry) == "cloud_login_failed"
