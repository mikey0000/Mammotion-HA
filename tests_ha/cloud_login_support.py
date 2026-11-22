"""Builders for setup tests that run the cloud login against a spec'd client.

Each setup (a reload included) is handed the client in the slot the test filled,
with the platforms and the device bring-up held back so only the login runs.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pymammotion.client import MammotionClient
from pymammotion.device.handle import DeviceRegistry
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.mammotion as mammotion_init
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)

ACCOUNT = "owner@example.com"
PASSWORD = "hunter2"
CACHED_SESSION = {"token": "cached"}
BLE_MOWER = "Luba-VSBLE1"
BLE_ADDRESS = "AA:BB:CC:DD:EE:01"


def make_cloud_client(*restore_results: Exception | None) -> MagicMock:
    """Return a client whose successive restores raise (or, for None, succeed)."""
    client = create_autospec(MammotionClient, instance=True)
    client.restore_credentials.side_effect = list(restore_results)
    client.reauth_required = None
    client.to_cache.return_value = {}
    client.aliyun_device_list = []
    client.mammotion_device_list = []
    client.mammotion_http = None
    client.unavailable_transports.return_value = {}
    # No handles, so setup builds no coordinators for the BLE mower.
    client.mower.return_value = None
    client.device_registry = create_autospec(DeviceRegistry, instance=True)
    client.device_registry.all_devices = []
    return client


def make_cloud_entry(
    hass: HomeAssistant, *, cached: bool = True, ble: bool = False
) -> MockConfigEntry:
    """Add an account entry, with a saved session and a BLE mower when asked."""
    data: dict[str, Any] = {
        CONF_ACCOUNTNAME: ACCOUNT,
        "password": PASSWORD,
        CONF_HAS_CLOUD_ACCOUNT: True,
    }
    if cached:
        data[CONF_AEP_DATA] = CACHED_SESSION
    if ble:
        data[CONF_BLE_DEVICES] = {BLE_MOWER: BLE_ADDRESS}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ACCOUNT, data=data)
    entry.add_to_hass(hass)
    return entry


@contextmanager
def serve_client(slot: list[MagicMock]) -> Iterator[None]:
    """Hand every setup the client in ``slot[0]``, with platforms and bring-up held back."""

    async def _bring_up(*_args: Any, **_kwargs: Any) -> None:
        return None

    with (
        patch.object(
            mammotion_init, "MammotionClient", side_effect=lambda **_: slot[0]
        ),
        patch.object(mammotion_init, "PLATFORMS", []),
        patch.object(mammotion_init, "_async_bring_up_devices", _bring_up),
    ):
        yield


def reauth_flows(hass: HomeAssistant) -> list[Any]:
    """Return the reauth flows in progress."""
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == "reauth"
    ]


def login_issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    """Return the entry's cloud-login repair, if one is shown."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"cloud_login_{entry.entry_id}")
