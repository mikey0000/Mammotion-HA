"""The options flow's go2rtc address, which turns camera re-streaming on and off."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import CONF_GO2RTC_URL, CONF_USE_WIFI, DOMAIN

_BASE_INPUT = {"prefer_ble_over_wifi": True, "notify": ["self_check"]}


def _entry(hass: HomeAssistant, **options: str) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USE_WIFI: True}, options=options)
    entry.add_to_hass(hass)
    return entry


async def test_a_go2rtc_address_is_saved_trimmed(hass: HomeAssistant) -> None:
    """Pasted addresses often carry a trailing space."""
    entry = _entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**_BASE_INPUT, CONF_GO2RTC_URL: " http://frigate:1984 "}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_GO2RTC_URL] == "http://frigate:1984"


async def test_an_address_go2rtc_cannot_be_reached_at_is_refused_then_corrected(
    hass: HomeAssistant,
) -> None:
    """The form names the field and saves once a usable address is entered."""
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**_BASE_INPUT, CONF_GO2RTC_URL: "frigate:1984"}
    )
    errors = result["errors"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**_BASE_INPUT, CONF_GO2RTC_URL: "http://frigate:1984"}
    )

    assert errors == {CONF_GO2RTC_URL: "invalid_go2rtc_url"}
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_GO2RTC_URL] == "http://frigate:1984"


async def test_clearing_the_address_turns_restreaming_off(hass: HomeAssistant) -> None:
    """The field is a suggestion, not a default, so an emptied field is not refilled."""
    entry = _entry(hass, **{CONF_GO2RTC_URL: "http://frigate:1984"})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _BASE_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_GO2RTC_URL not in entry.options


@pytest.mark.parametrize(
    ("options", "suggested"),
    [
        pytest.param(
            {CONF_GO2RTC_URL: "http://frigate:1984"}, "http://frigate:1984", id="set"
        ),
        pytest.param({}, None, id="unset"),
    ],
)
async def test_the_form_suggests_the_saved_address(
    hass: HomeAssistant, options: dict[str, str], suggested: str | None
) -> None:
    """Reopening the options shows what is saved, and nothing when unset."""
    entry = _entry(hass, **options)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    field = next(k for k in result["data_schema"].schema if k == CONF_GO2RTC_URL)
    assert field.description == {"suggested_value": suggested}
