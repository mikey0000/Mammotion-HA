"""A setup login that fails transiently is retried by Home Assistant, not abandoned.

pymammotion raises ``ConnectionError`` for HTTP 408/429/5xx from ``oauth2/token``
and lets timeouts and aiohttp disconnects through as their own types; it documents
all of them as "back off and retry".  Without a BLE fallback the only retry Home
Assistant offers is ``ConfigEntryNotReady``: returning False instead leaves the entry
loaded with no mowers until the next restart.  The exceptions are the real ones.
"""

from collections.abc import Callable
from unittest.mock import MagicMock, create_autospec

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from pymammotion.client import MammotionClient
from pymammotion.transport.base import ReLoginRequiredError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _async_attempt_login
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"

_TRANSIENT: list[Callable[[], Exception]] = [
    lambda: ConnectionError("oauth2/token answered HTTP 503"),
    TimeoutError,
    aiohttp.ConnectionTimeoutError,
    aiohttp.ServerDisconnectedError,
]
_TRANSIENT_IDS = ["http_503", "timeout", "connection_timeout", "server_disconnected"]
transient = pytest.mark.parametrize("make_exc", _TRANSIENT, ids=_TRANSIENT_IDS)


def _entry(hass: HomeAssistant, *, cached: bool) -> MockConfigEntry:
    data: dict[str, object] = {CONF_ACCOUNTNAME: _ACCOUNT, CONF_HAS_CLOUD_ACCOUNT: True}
    if cached:
        data[CONF_AEP_DATA] = {"token": "stale"}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=_ACCOUNT, data=data)
    entry.add_to_hass(hass)
    return entry


def _client(
    *, restore: Exception | None = None, login: Exception | None = None
) -> MagicMock:
    client = create_autospec(MammotionClient, instance=True)
    client.restore_credentials.side_effect = restore
    client.login_and_initiate_cloud.side_effect = login
    return client


def _stale_cache() -> ReLoginRequiredError:
    """Return what restoring a cache the server no longer accepts raises."""
    return ReLoginRequiredError(_ACCOUNT, "refresh token rejected")


async def _attempt(
    hass: HomeAssistant, entry: MockConfigEntry, client: object, *, ble_fallback: bool
) -> bool:
    return await _async_attempt_login(
        hass, entry, client, _ACCOUNT, "password", ble_fallback=ble_fallback
    )


@pytest.mark.regression
@transient
async def test_a_transient_login_failure_without_ble_is_retried(
    hass: HomeAssistant, make_exc: Callable[[], Exception]
) -> None:
    """The catch-all returned False, so setup "succeeded" with no mowers and no retry."""
    entry = _entry(hass, cached=False)
    exc = make_exc()

    with pytest.raises(ConfigEntryNotReady) as raised:
        await _attempt(hass, entry, _client(login=exc), ble_fallback=False)

    assert raised.value.__cause__ is exc


@transient
async def test_a_transient_login_failure_with_ble_continues_ble_only(
    hass: HomeAssistant, make_exc: Callable[[], Exception]
) -> None:
    """BLE mowers keep working, so the cloud is dropped for this setup only."""
    entry = _entry(hass, cached=False)

    assert (
        await _attempt(hass, entry, _client(login=make_exc()), ble_fallback=True)
        is False
    )


@pytest.mark.regression
async def test_an_unexpected_login_failure_without_ble_fails_setup(
    hass: HomeAssistant,
) -> None:
    """A non-network fault also returned False and left a loaded, empty entry."""
    entry = _entry(hass, cached=False)
    exc = ValueError("malformed login payload")

    with pytest.raises(ConfigEntryError) as raised:
        await _attempt(hass, entry, _client(login=exc), ble_fallback=False)

    assert type(raised.value) is ConfigEntryError
    assert raised.value.__cause__ is exc


@pytest.mark.regression
@transient
async def test_a_transient_failure_of_the_stale_cache_relogin_without_ble_is_retried(
    hass: HomeAssistant, make_exc: Callable[[], Exception]
) -> None:
    """The re-login caught only auth errors, so this escaped setup as setup_error."""
    entry = _entry(hass, cached=True)
    exc = make_exc()
    client = _client(restore=_stale_cache(), login=exc)

    with pytest.raises(ConfigEntryNotReady) as raised:
        await _attempt(hass, entry, client, ble_fallback=False)

    assert raised.value.__cause__ is exc
    client.login_and_initiate_cloud.assert_awaited_once()


@pytest.mark.regression
@transient
async def test_a_transient_failure_of_the_stale_cache_relogin_with_ble_continues_ble_only(
    hass: HomeAssistant, make_exc: Callable[[], Exception]
) -> None:
    """It escaped setup even though the BLE mowers could have carried on."""
    entry = _entry(hass, cached=True)
    client = _client(restore=_stale_cache(), login=make_exc())

    assert await _attempt(hass, entry, client, ble_fallback=True) is False


@pytest.mark.regression
async def test_an_unexpected_failure_of_the_stale_cache_relogin_fails_setup(
    hass: HomeAssistant,
) -> None:
    """A non-network fault in the re-login is a setup error, not a retry."""
    entry = _entry(hass, cached=True)
    exc = ValueError("malformed login payload")
    client = _client(restore=_stale_cache(), login=exc)

    with pytest.raises(ConfigEntryError) as raised:
        await _attempt(hass, entry, client, ble_fallback=False)

    assert type(raised.value) is ConfigEntryError
    assert raised.value.__cause__ is exc


async def test_a_rejected_stale_cache_relogin_still_asks_for_reauthentication(
    hass: HomeAssistant,
) -> None:
    """Transient handling must not swallow the reauth signal."""
    entry = _entry(hass, cached=True)
    client = _client(restore=_stale_cache(), login=_stale_cache())

    with pytest.raises(ConfigEntryAuthFailed):
        await _attempt(hass, entry, client, ble_fallback=False)


@pytest.mark.regression
@transient
async def test_a_transient_restore_failure_without_ble_is_retried(
    hass: HomeAssistant, make_exc: Callable[[], Exception]
) -> None:
    """Restoring the cache hit the same catch-all as a fresh login."""
    entry = _entry(hass, cached=True)
    exc = make_exc()

    with pytest.raises(ConfigEntryNotReady) as raised:
        await _attempt(hass, entry, _client(restore=exc), ble_fallback=False)

    assert raised.value.__cause__ is exc


@transient
async def test_a_transient_restore_failure_keeps_the_cached_credentials(
    hass: HomeAssistant, make_exc: Callable[[], Exception]
) -> None:
    """The server never judged the cache, so the retry must be able to use it."""
    entry = _entry(hass, cached=True)

    assert (
        await _attempt(hass, entry, _client(restore=make_exc()), ble_fallback=True)
        is False
    )
    assert entry.data[CONF_AEP_DATA] == {"token": "stale"}


@pytest.mark.parametrize("cached", [False, True], ids=["login", "stale_cache_relogin"])
async def test_an_unexpected_login_failure_with_ble_continues_ble_only(
    hass: HomeAssistant, cached: bool
) -> None:
    """With BLE mowers the entry still loads; the fault is only logged."""
    entry = _entry(hass, cached=cached)
    exc = ValueError("malformed login payload")
    client = _client(restore=_stale_cache(), login=exc)

    assert await _attempt(hass, entry, client, ble_fallback=True) is False
