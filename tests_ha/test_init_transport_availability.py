"""A cloud transport given up on a healthy login shows a repair until it recovers.

pymammotion reports each give-up and recovery through
``on_transport_availability_changed`` and answers ``unavailable_transports`` after
a restore.  The login and its saved session stay; only the repair, naming the
mowers that transport carries, tells the user what is wrong.
"""

from collections.abc import AsyncIterator, Iterator
from unittest.mock import MagicMock, create_autospec

import pytest
from cloud_login_support import (
    ACCOUNT,
    CACHED_SESSION,
    make_cloud_client,
    make_cloud_entry,
    reauth_flows,
    serve_client,
)
from device_list_support import account_device
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pymammotion.http.http import MammotionHTTP
from pymammotion.http.model.http import DeviceInfo
from pymammotion.transport.base import TransportType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import CONF_AEP_DATA, DOMAIN

_ALIYUN_MOWER = "Luba-VSALI1"
_MAMMOTION_MOWER = "Luba-VSMAM1"


@pytest.fixture
def client() -> Iterator[MagicMock]:
    """Serve every setup a client listing one mower on each cloud."""
    served = make_cloud_client(None)
    served.aliyun_device_list = [account_device(_ALIYUN_MOWER, "")]
    served.mammotion_device_list = [account_device(_MAMMOTION_MOWER, "")]
    with serve_client([served]):
        yield served


@pytest.fixture
async def entry(
    hass: HomeAssistant, client: MagicMock
) -> AsyncIterator[MockConfigEntry]:
    """Load the account's entry, and unload it after the test."""
    loaded = make_cloud_entry(hass)
    assert await hass.config_entries.async_setup(loaded.entry_id)
    await hass.async_block_till_done()
    yield loaded
    if loaded.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(loaded.entry_id)


def _issue(
    hass: HomeAssistant, entry: MockConfigEntry, transport_type: TransportType
) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"cloud_transport_unavailable_{entry.entry_id}_{transport_type.value}"
    )


async def _report(
    client: MagicMock, transport_type: TransportType, available: bool
) -> None:
    await client.on_transport_availability_changed(
        ACCOUNT, transport_type, available, None if available else "refused"
    )


async def test_a_given_up_transport_shows_a_fixable_repair_naming_its_mowers(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """The user learns which mowers are dark and that the integration needs attention."""
    await _report(client, TransportType.CLOUD_ALIYUN, available=False)

    issue = _issue(hass, entry, TransportType.CLOUD_ALIYUN)
    assert issue is not None
    assert issue.translation_key == "cloud_transport_unavailable"
    assert issue.is_fixable
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders == {
        "account": ACCOUNT,
        "service": "Aliyun IoT",
        "mowers": _ALIYUN_MOWER,
    }
    assert issue.data == {"entry_id": entry.entry_id, "transport": "cloud_aliyun"}


async def test_a_given_up_transport_keeps_the_login_and_its_saved_session(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """The login is healthy, so neither its cache nor a reauth prompt is touched."""
    await _report(client, TransportType.CLOUD_MAMMOTION, available=False)
    await hass.async_block_till_done()

    assert entry.data[CONF_AEP_DATA] == CACHED_SESSION
    assert reauth_flows(hass) == []


async def test_a_recovered_transport_clears_its_repair(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """The repair goes by itself once the library reports the transport back."""
    await _report(client, TransportType.CLOUD_MAMMOTION, available=False)

    await _report(client, TransportType.CLOUD_MAMMOTION, available=True)

    assert _issue(hass, entry, TransportType.CLOUD_MAMMOTION) is None


async def test_unloading_clears_the_repair(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """A reload decides afresh; a repair left behind would outlive its client."""
    await _report(client, TransportType.CLOUD_ALIYUN, available=False)

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert _issue(hass, entry, TransportType.CLOUD_ALIYUN) is None


@pytest.mark.regression
async def test_setup_shows_the_repair_for_a_transport_the_restore_gave_up(
    hass: HomeAssistant, client: MagicMock
) -> None:
    """A restore that gave a transport up reported success and the mowers went dark silently."""
    client.unavailable_transports.return_value = {
        TransportType.CLOUD_MAMMOTION: "no JWT"
    }
    entry = make_cloud_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    issue = _issue(hass, entry, TransportType.CLOUD_MAMMOTION)
    assert issue is not None
    assert issue.translation_placeholders["mowers"] == _MAMMOTION_MOWER
    assert _issue(hass, entry, TransportType.CLOUD_ALIYUN) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_clears_a_repair_whose_transport_came_back(
    hass: HomeAssistant, client: MagicMock
) -> None:
    """A repair raised before the restart must not outlive a healthy restore."""
    entry = make_cloud_entry(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"cloud_transport_unavailable_{entry.entry_id}_cloud_aliyun",
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="cloud_transport_unavailable",
    )

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert _issue(hass, entry, TransportType.CLOUD_ALIYUN) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_repair_names_owned_mowers_the_aliyun_listing_is_missing(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """A login whose Aliyun half was refused saved no listing; the owned list still names them."""
    client.aliyun_device_list = []
    client.mammotion_http = create_autospec(MammotionHTTP, instance=True)
    client.mammotion_http.device_info = [
        DeviceInfo(device_name=_MAMMOTION_MOWER),
        DeviceInfo(device_name="Luba-VSALI2"),
    ]

    await _report(client, TransportType.CLOUD_ALIYUN, available=False)

    issue = _issue(hass, entry, TransportType.CLOUD_ALIYUN)
    assert issue is not None
    assert issue.translation_placeholders["mowers"] == "Luba-VSALI2"
