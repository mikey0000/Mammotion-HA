"""The camera platform turning go2rtc re-streaming on from the entry's options."""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace

import pytest
from agora_session_support import make_camera_coordinator
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from restream_support import (
    GO2RTC_STREAMS,
    GO2RTC_URL,
    HA_INTERNAL_URL,
    STREAM_LEFT,
    STREAM_REAR,
    STREAM_RIGHT,
)

from custom_components.mammotion.camera import (
    MammotionWebRTCCamera,
    async_setup_entry,
)
from custom_components.mammotion.const import CONF_GO2RTC_URL, DOMAIN
from custom_components.mammotion.models import MammotionDevices


@pytest.fixture(autouse=True)
async def http(hass: HomeAssistant) -> None:
    """Serve Home Assistant's HTTP views at a known internal URL."""
    assert await async_setup_component(hass, "http", {})
    await hass.config.async_update(internal_url=HA_INTERNAL_URL)


def _collect(added: list[MammotionWebRTCCamera]) -> AddEntitiesCallback:
    def add(entities: Iterable[Entity], update_before_add: bool = False) -> None:
        added.extend(entities)  # type: ignore[arg-type]

    return add


def _yuka_entry(hass: HomeAssistant, options: dict[str, str]) -> MockConfigEntry:
    coordinator = make_camera_coordinator()
    mower = SimpleNamespace(
        device=coordinator.device, reporting_coordinator=coordinator
    )
    entry = MockConfigEntry(domain=DOMAIN, options=options)
    entry.add_to_hass(hass)
    entry.runtime_data = MammotionDevices(mowers=[mower], RTK=[], spino=[])  # type: ignore[list-item]
    return entry


async def test_a_go2rtc_address_registers_every_camera_of_the_mower(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Each camera entity the platform adds becomes a go2rtc stream."""
    aioclient_mock.get(GO2RTC_STREAMS, json={})
    aioclient_mock.patch(GO2RTC_STREAMS)
    entry = _yuka_entry(hass, {CONF_GO2RTC_URL: GO2RTC_URL})
    added: list[MammotionWebRTCCamera] = []

    await async_setup_entry(hass, entry, _collect(added))
    await hass.async_block_till_done(wait_background_tasks=True)

    patched = {
        url.query["name"]
        for method, url, *_ in aioclient_mock.mock_calls
        if method == "patch"
    }
    assert len(added) == 3
    assert patched == {STREAM_LEFT, STREAM_RIGHT, STREAM_REAR}


async def test_without_a_go2rtc_address_nothing_reaches_go2rtc(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Re-streaming is opt-in: existing installs see no new traffic."""
    entry = _yuka_entry(hass, {})

    await async_setup_entry(hass, entry, _collect([]))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert aioclient_mock.call_count == 0
