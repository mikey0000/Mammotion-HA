"""Fixing the cloud-transport repair against the fake cloud."""

from typing import Any

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pymammotion.transport.base import TransportType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.fakeserver.cloud import FakeMammotionCloud
from tests.integration.fake_cloud._helpers import counts, make_aliyun_account

from custom_components.mammotion.const import DOMAIN

from .fake_cloud_support import (
    entry_client,
    make_cache,
    make_saved_entry,
    settle,
    transport_issue,
)


@pytest.fixture
async def entry(hass: HomeAssistant, fake_cloud: FakeMammotionCloud) -> MockConfigEntry:
    """Set up a hybrid account whose Aliyun login the cloud refuses, showing the repair."""
    assert await async_setup_component(hass, "repairs", {})
    scenario = fake_cloud.scenario
    make_aliyun_account(scenario, hybrid=True)
    scenario.faults["aliyun_session"] = 2064
    loaded = make_saved_entry(hass, scenario, await make_cache(fake_cloud))
    await hass.config_entries.async_setup(loaded.entry_id)
    await hass.async_block_till_done()
    await settle(entry_client(loaded))
    assert transport_issue(hass, loaded, TransportType.CLOUD_ALIYUN) is not None
    await fake_cloud.baseline_counters()
    return loaded


async def _retry(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    manager = repairs_flow_manager(hass)
    assert manager is not None
    issue = transport_issue(hass, entry, TransportType.CLOUD_ALIYUN)
    assert issue is not None
    flow = await manager.async_init(DOMAIN, context={"issue_id": issue.issue_id})
    result = await manager.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    return result


async def test_a_retry_the_cloud_accepts_clears_the_repair_without_a_password(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud, entry: MockConfigEntry
) -> None:
    """The authCode chain from the healthy login mints the Aliyun session again."""
    del fake_cloud.scenario.faults["aliyun_session"]

    result = await _retry(hass, entry)
    await settle(entry_client(entry))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert transport_issue(hass, entry, TransportType.CLOUD_ALIYUN) is None
    assert counts(fake_cloud.scenario, "password_grants") == {"password_grants": 0}


async def test_a_retry_the_cloud_refuses_asks_for_the_password(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud, entry: MockConfigEntry
) -> None:
    """A refusal from the healthy login leaves a sign-in as the only cure."""
    result = await _retry(hass, entry)

    assert result["step_id"] == "password"
    assert counts(fake_cloud.scenario, "password_grants") == {"password_grants": 0}


async def test_signing_in_from_the_repair_logs_in_once_and_reloads_from_it(
    hass: HomeAssistant, fake_cloud: FakeMammotionCloud, entry: MockConfigEntry
) -> None:
    """The password step's login is saved; the reload restores it, not logs in again."""
    result = await _retry(hass, entry)
    del fake_cloud.scenario.faults["aliyun_session"]
    manager = repairs_flow_manager(hass)
    assert manager is not None

    result = await manager.async_configure(
        result["flow_id"], {CONF_PASSWORD: fake_cloud.scenario.password}
    )
    await hass.async_block_till_done()
    await settle(entry_client(entry))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert transport_issue(hass, entry, TransportType.CLOUD_ALIYUN) is None
    assert counts(fake_cloud.scenario, "password_grants") == {"password_grants": 1}
