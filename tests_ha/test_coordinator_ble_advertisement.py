"""What the report coordinator does with its mower's advertisements.

Each advertisement is handed to pymammotion's BLE transport and, when the link is
down, starts a connect.  That connect can take most of a minute through an ESPHome
proxy, and Home Assistant replays the last advertisement the moment the callback is
registered, so it has to run where startup does not wait for it and where the
Bluetooth switch can still stop it.
"""

import asyncio
from datetime import timedelta
from unittest.mock import create_autospec

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceHandle
from pymammotion.transport.ble import BLETransport
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.const import CONF_BLE_DEVICES, DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from tests_ha.ble_advertisements import inject_advertisement

_MOWER = "Luba-VS123456"
_MOWER_MAC = "AA:BB:CC:DD:EE:FF"
_WAIT = 5


def _coordinator(
    hass: HomeAssistant, ble: BLETransport
) -> MammotionReportUpdateCoordinator:
    """Build the real report coordinator over a spec'd client whose mower has *ble*."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_MOWER_MAC.lower(),
        data={CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC.lower()}},
    )
    entry.add_to_hass(hass)
    device = MowingDevice(name=_MOWER)
    device.mower_state.ble_mac = _MOWER_MAC.lower()
    handle = create_autospec(DeviceHandle, instance=True)
    handle.get_transport.return_value = ble
    manager = create_autospec(MammotionClient, instance=True)
    manager.get_device_by_name.return_value = device
    manager.mower.return_value = handle
    return MammotionReportUpdateCoordinator(
        hass, entry, _create_ble_only_device(_MOWER), manager, unique_name=_MOWER
    )


def _idle_ble() -> BLETransport:
    ble = create_autospec(BLETransport, instance=True)
    ble.is_connected = False
    return ble


@pytest.mark.regression
@pytest.mark.usefixtures("enable_bluetooth")
async def test_startup_does_not_wait_for_the_advertisement_connect(
    hass: HomeAssistant,
) -> None:
    """The connect ran as a tracked task, holding startup for ~45 s (#929).

    Startup's "waiting for startup to wrap up" is ``async_block_till_done``: it
    waited on the connect the replayed advertisement started through a proxy.
    """
    ble = _idle_ble()
    connecting = asyncio.Event()
    release = asyncio.Event()

    async def slow_connect() -> None:
        connecting.set()
        await release.wait()

    ble.connect.side_effect = slow_connect
    coordinator = _coordinator(hass, ble)
    coordinator._async_start()

    inject_advertisement(hass, _MOWER, _MOWER_MAC)
    await asyncio.wait_for(connecting.wait(), timeout=_WAIT)
    # Released whatever happens, or a tracked connect would hang the teardown too.
    try:
        await asyncio.wait_for(hass.async_block_till_done(), timeout=_WAIT)
    finally:
        release.set()
        await coordinator.async_shutdown()
        await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.regression
@pytest.mark.usefixtures("enable_bluetooth")
async def test_bluetooth_switched_off_mid_handoff_does_not_connect(
    hass: HomeAssistant,
) -> None:
    """The switch went off while the advertisement was being handed over; it connected anyway.

    The enabled check ran before the hand-off's await, and the hand-off re-creates
    the transport the switch had just detached, so the connect that followed held a
    proxy slot with Bluetooth off.
    """
    ble = _idle_ble()
    coordinator = _coordinator(hass, ble)
    handing_over = asyncio.Event()
    release = asyncio.Event()

    async def slow_handoff(*_args: object) -> bool:
        handing_over.set()
        await release.wait()
        return False

    coordinator.manager.update_ble_device.side_effect = slow_handoff
    coordinator._async_start()

    inject_advertisement(hass, _MOWER, _MOWER_MAC)
    await asyncio.wait_for(handing_over.wait(), timeout=_WAIT)
    await coordinator.async_set_bluetooth_enabled(False)
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    ble.connect.assert_not_awaited()
    await coordinator.async_shutdown()


@pytest.mark.regression
@pytest.mark.usefixtures("enable_bluetooth")
async def test_no_advertisement_is_handed_over_after_shutdown(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The debounced hand-off outlived the coordinator and ran against a torn-down client.

    An advertisement during the cooldown queued a call for its end, and shutdown
    stopped every debouncer but this one.
    """
    coordinator = _coordinator(hass, _idle_ble())
    coordinator._async_start()
    inject_advertisement(hass, _MOWER, _MOWER_MAC)
    await hass.async_block_till_done(wait_background_tasks=True)
    inject_advertisement(hass, _MOWER, _MOWER_MAC)
    await hass.async_block_till_done(wait_background_tasks=True)

    await coordinator.async_shutdown()
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    coordinator.manager.update_ble_device.assert_awaited_once()
