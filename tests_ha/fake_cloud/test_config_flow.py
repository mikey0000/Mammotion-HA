"""Signing in from a flow against the fake cloud: the one password grant setup reuses."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from tests.fakeserver.cloud import FakeMammotionCloud
from tests.integration.fake_cloud._helpers import counts, make_aliyun_account

from .fake_cloud_support import (
    add_account,
    entry_client,
    make_cache,
    make_saved_entry,
    settle,
)

pytestmark = pytest.mark.usefixtures("enable_bluetooth")


async def test_adding_the_account_signs_in_once_and_setup_reuses_that_login(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """The flow's login seeds the cache, so setup does not log in a second time."""
    make_aliyun_account(fake_cloud.scenario)

    entry = (await add_account(hass, fake_cloud.scenario))["result"]
    await settle(entry_client(entry))

    assert entry.state is ConfigEntryState.LOADED
    assert counts(fake_cloud.scenario, "password_grants") == {"password_grants": 1}


async def test_reauthenticating_signs_in_once_and_the_reload_reuses_that_login(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud
) -> None:
    """The reauth login seeds the cache, so the reload sends no second password."""
    scenario = fake_cloud.scenario
    entry = make_saved_entry(hass, scenario, await make_cache(fake_cloud))
    scenario.expire_session()
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    scenario.refresh_rejected = False

    flow = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSWORD: scenario.password}
    )
    await hass.async_block_till_done()
    await settle(entry_client(entry))

    assert result["reason"] == "reauth_successful"
    assert entry.state is ConfigEntryState.LOADED
    assert counts(scenario, "password_grants") == {"password_grants": 1}
