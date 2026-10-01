"""Clearing the credential cache removes every key a restore reads.

The Mammotion device list and records are part of the cache ``to_cache()`` writes
and ``restore_credentials`` reads; left behind after a clear they describe a
session the server has rejected, and the records are one of the sentinels
``_load_cached_credentials`` keys on.
"""

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import (
    _clear_cached_credentials,
    _load_cached_credentials,
)
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_HAS_CLOUD_ACCOUNT,
    CONF_MAMMOTION_DATA,
    CONF_MAMMOTION_DEVICE_LIST,
    CONF_MAMMOTION_DEVICE_RECORDS,
    CONF_MAMMOTION_MQTT,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"


def _cached_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            CONF_HAS_CLOUD_ACCOUNT: True,
            CONF_AEP_DATA: {"token": "aliyun"},
            CONF_MAMMOTION_DATA: {"token": "http"},
            CONF_MAMMOTION_MQTT: {"token": "jwt"},
            CONF_MAMMOTION_DEVICE_LIST: [{"device_name": "Luba-VS1"}],
            CONF_MAMMOTION_DEVICE_RECORDS: {"records": [{"device_name": "Luba-VS1"}]},
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.regression
async def test_clearing_the_cache_removes_the_device_list_and_records(
    hass: HomeAssistant,
) -> None:
    """Both were missing from CREDENTIAL_CACHE_KEYS, so a clear left them behind."""
    entry = _cached_entry(hass)

    _clear_cached_credentials(hass, entry)

    assert CONF_MAMMOTION_DEVICE_LIST not in entry.data
    assert CONF_MAMMOTION_DEVICE_RECORDS not in entry.data
    assert entry.data[CONF_ACCOUNTNAME] == _ACCOUNT


async def test_a_cleared_cache_is_not_treated_as_usable(hass: HomeAssistant) -> None:
    """Setup must not hand a cleared cache to restore_credentials."""
    entry = _cached_entry(hass)
    assert _load_cached_credentials(entry)

    _clear_cached_credentials(hass, entry)

    assert _load_cached_credentials(entry) == {}
