"""Builders for the fake-cloud tier; imported only after its conftest's skips pass."""

from typing import Any

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.json import json_dumps
from homeassistant.util.json import json_loads
from pymammotion.client import MammotionClient
from pymammotion.transport.base import TransportType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests._helpers import wait_until
from tests.fakeserver.cloud import FakeMammotionCloud
from tests.fakeserver.scenario import Scenario

from custom_components.mammotion.const import (
    CONF_ACCOUNT_ID,
    CONF_ACCOUNTNAME,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_USE_WIFI,
    DOMAIN,
)

#: Ceiling on a transport connecting to the loopback broker.
_SETTLE_TIMEOUT = 5.0

async def add_account(hass: HomeAssistant, scenario: Scenario) -> ConfigFlowResult:
    """Drive Add Integration with nothing in Bluetooth range, signing in as the fake account."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "wifi"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ACCOUNTNAME: scenario.account, CONF_PASSWORD: scenario.password},
    )
    await hass.async_block_till_done()
    return result


def make_saved_entry(
    hass: HomeAssistant, scenario: Scenario, cache: dict[str, Any]
) -> MockConfigEntry:
    """Add the account's entry holding *cache* as Home Assistant would have saved it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=scenario.user_account,
        title=scenario.account,
        data={
            CONF_ACCOUNTNAME: scenario.account,
            CONF_PASSWORD: scenario.password,
            CONF_ACCOUNT_ID: scenario.user_account,
            CONF_USE_WIFI: True,
            CONF_HAS_CLOUD_ACCOUNT: True,
            **as_saved(cache),
        },
    )
    entry.add_to_hass(hass)
    return entry


def as_saved(data: dict[str, Any]) -> dict[str, Any]:
    """Return *data* as it reads back from disk after a restart."""
    loaded = json_loads(json_dumps(data))
    assert isinstance(loaded, dict)
    return loaded


def transport_issue(
    hass: HomeAssistant, entry: ConfigEntry, transport_type: TransportType
) -> ir.IssueEntry | None:
    """Return the repair shown while *transport_type* is given up, if any."""
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"cloud_transport_unavailable_{entry.entry_id}_{transport_type.value}"
    )


def _transports_settled(client: MammotionClient) -> bool:
    """Whether no cloud transport of *client* is still mid-connect."""
    return all(
        handle.get_transport(transport_type) is None
        or handle.is_transport_connected(transport_type)
        or not handle.get_transport(transport_type).is_usable
        for handle in client.device_registry.all_devices
        for transport_type in (
            TransportType.CLOUD_ALIYUN,
            TransportType.CLOUD_MAMMOTION,
        )
    )


async def settle(client: MammotionClient) -> None:
    """Wait until no cloud transport is mid-connect.

    Stopping a client before its MQTT CONNACK leaves aiomqtt's socket behind
    (a pymammotion defect, reported); waiting keeps the tests off that race.
    """
    await wait_until(lambda: _transports_settled(client), timeout=_SETTLE_TIMEOUT)


async def make_cache(cloud: FakeMammotionCloud) -> dict[str, Any]:
    """Log in on a throwaway client and return its cache, with the counters zeroed once idle."""
    client = MammotionClient()
    try:
        await client.login_and_initiate_cloud(
            cloud.scenario.account, cloud.scenario.password
        )
        await settle(client)
        cache = client.to_cache()
    finally:
        await client.stop()
    await cloud.baseline_counters()
    return cache


def entry_client(entry: ConfigEntry) -> MammotionClient:
    """Return the client a loaded entry runs."""
    client = entry.runtime_data.client
    assert client is not None, "the entry was not loaded"
    return client


def mower_state(hass: HomeAssistant, device_name: str) -> str | None:
    """Return the state of the mower's lawn_mower entity, None when it has none."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "lawn_mower", DOMAIN, f"{device_name}_mower"
    )
    if entity_id is None or (state := hass.states.get(entity_id)) is None:
        return None
    return state.state
