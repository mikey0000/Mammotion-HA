"""Setup against the fake cloud: it restores the saved login and never sends the password.

The counters are the server's, so they see every grant and refresh the library made.
"""

from datetime import timedelta

import pytest
from cloud_login_support import reauth_flows
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pymammotion.transport.base import TransportType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from tests.fakeserver.cloud import FakeMammotionCloud
from tests.integration.fake_cloud._helpers import counts, make_aliyun_account

from .fake_cloud_support import (
    entry_client,
    make_cache,
    make_saved_entry,
    mower_state,
    settle,
    transport_issue,
)

#: Past Home Assistant's first setup retry.
_RETRY_DELAY = timedelta(minutes=5)


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _home_assistant_retries(hass: HomeAssistant) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + _RETRY_DELAY)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_restart_restores_the_saved_login_without_a_password_grant(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """A healthy saved login costs neither a password grant nor a refresh."""
    scenario = fake_cloud.scenario
    entry = make_saved_entry(hass, scenario, await make_cache(fake_cloud))

    await _set_up(hass, entry)
    await settle(entry_client(entry))

    assert entry.state is ConfigEntryState.LOADED
    assert counts(scenario, "password_grants", "refresh_grants") == {
        "password_grants": 0,
        "refresh_grants": 0,
    }


@pytest.mark.regression
async def test_setup_without_a_saved_login_asks_to_sign_in_and_sends_no_password(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """With no cache, setup logged in with the stored password on every start."""
    entry = make_saved_entry(hass, fake_cloud.scenario, {})

    await _set_up(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(reauth_flows(hass)) == 1
    assert counts(fake_cloud.scenario, "password_grants") == {"password_grants": 0}


async def test_a_login_the_server_ended_asks_to_sign_in_and_sends_no_password(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """Signed in elsewhere: the bearer and refresh token are dead, the password unused."""
    scenario = fake_cloud.scenario
    entry = make_saved_entry(hass, scenario, await make_cache(fake_cloud))
    scenario.expire_session()

    await _set_up(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(reauth_flows(hass)) == 1
    assert counts(scenario, "password_grants") == {"password_grants": 0}


async def test_an_unreachable_cloud_is_retried_from_the_saved_login_alone(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """Each of Home Assistant's retries refreshes once and never sends the password."""
    scenario = fake_cloud.scenario
    cache = await make_cache(fake_cloud)
    scenario.age_cached_access_token(cache)
    entry = make_saved_entry(hass, scenario, cache)
    scenario.oauth_http_status = 503

    await _set_up(hass, entry)
    await _home_assistant_retries(hass)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert counts(scenario, "password_grants", "oauth_503") == {
        "password_grants": 0,
        "oauth_503": 2,
    }


async def test_setup_recovers_once_the_cloud_is_back(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """The retry that finds the cloud back refreshes once and loads."""
    scenario = fake_cloud.scenario
    cache = await make_cache(fake_cloud)
    scenario.age_cached_access_token(cache)
    entry = make_saved_entry(hass, scenario, cache)
    scenario.oauth_http_status = 503
    await _set_up(hass, entry)

    scenario.oauth_http_status = None
    await _home_assistant_retries(hass)
    await settle(entry_client(entry))

    assert entry.state is ConfigEntryState.LOADED
    assert counts(scenario, "password_grants", "refresh_grants") == {
        "password_grants": 0,
        "refresh_grants": 1,
    }


async def test_a_rate_limited_refresh_costs_one_request_per_attempt_and_no_password(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """No retry loop inside an attempt.

    pymammotion raises an oauth2/token 429 as a plain ConnectionError, so it is
    retried on Home Assistant's backoff like a 503 rather than surfaced.
    """
    scenario = fake_cloud.scenario
    cache = await make_cache(fake_cloud)
    scenario.age_cached_access_token(cache)
    entry = make_saved_entry(hass, scenario, cache)
    scenario.oauth_http_status = 429

    await _set_up(hass, entry)
    await _home_assistant_retries(hass)

    assert counts(scenario, "password_grants", "oauth_429") == {
        "password_grants": 0,
        "oauth_429": 2,
    }
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def _restore_with_aliyun_refused(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> MockConfigEntry:
    """Set up a hybrid account whose saved Aliyun session the cloud refuses to renew."""
    scenario = fake_cloud.scenario
    make_aliyun_account(scenario, hybrid=True)
    cache = await make_cache(fake_cloud)
    scenario.age_cached_aliyun_session(cache)
    scenario.revoke_aliyun_session()
    scenario.faults["aliyun_session"] = 2064
    entry = make_saved_entry(hass, scenario, cache)
    await _set_up(hass, entry)
    await settle(entry_client(entry))
    return entry


@pytest.mark.regression
async def test_an_aliyun_refusal_at_restore_shows_the_repair_naming_its_mower(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """The restore reported success while the Aliyun mower went dark with no message."""
    entry = await _restore_with_aliyun_refused(hass, fake_cloud)

    issue = transport_issue(hass, entry, TransportType.CLOUD_ALIYUN)
    assert issue is not None
    assert issue.translation_placeholders["mowers"] == "Luba-ALIYUN1"
    assert transport_issue(hass, entry, TransportType.CLOUD_MAMMOTION) is None
    assert reauth_flows(hass) == []
    assert counts(fake_cloud.scenario, "password_grants") == {"password_grants": 0}


async def test_an_aliyun_refusal_keeps_the_aliyun_mower_as_an_unavailable_entity(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """The mower stays in Home Assistant, shown unavailable, not removed."""
    await _restore_with_aliyun_refused(hass, fake_cloud)

    assert mower_state(hass, "Luba-ALIYUN1") == STATE_UNAVAILABLE
    assert mower_state(hass, fake_cloud.scenario.device.device_name) not in (
        None,
        STATE_UNAVAILABLE,
    )


@pytest.mark.regression
async def test_a_login_saved_without_its_aliyun_half_shows_the_repair_after_setup(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """A flow's partial hybrid login was saved and set up as if healthy."""
    scenario = fake_cloud.scenario
    make_aliyun_account(scenario, hybrid=True)
    scenario.faults["aliyun_session"] = 2064
    entry = make_saved_entry(hass, scenario, await make_cache(fake_cloud))

    await _set_up(hass, entry)
    await settle(entry_client(entry))

    issue = transport_issue(hass, entry, TransportType.CLOUD_ALIYUN)
    assert issue is not None
    assert issue.translation_placeholders["mowers"] == "Luba-ALIYUN1"
    assert entry.state is ConfigEntryState.LOADED
