"""Pushed device state reaches the entry store, so a restart restores what the device last said.

Only the poll path used to save, and every push reschedules the poll
(``DataUpdateCoordinator.async_set_updated_data``); with a push roughly every 16 s
the 5-minute poll never ran.  The real report coordinator runs here over the
real entry store.
"""

from datetime import timedelta
from typing import Any
from unittest.mock import create_autospec

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_info import ChargeSettings
from pymammotion.state.device_state import DeviceStateMachine
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.config import async_get_store
from custom_components.mammotion.const import CONF_BLE_DEVICES, DOMAIN
from custom_components.mammotion.coordinator import (
    STATE_PUSH_SAVE_INTERVAL,
    MammotionBaseUpdateCoordinator,
    MammotionMapUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)

_MOWER = "Luba-VS123456"


async def _coordinator(
    hass: HomeAssistant,
    coordinator_cls: type[
        MammotionBaseUpdateCoordinator[Any]
    ] = MammotionReportUpdateCoordinator,
    library_device: MowingDevice | None = None,
) -> Any:
    """Build a real coordinator over the loaded entry store.

    With no *library_device* the client knows no device, so shutdown falls back
    to the coordinator's ``data``.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-1",
        unique_id="aa:bb:cc:dd:ee:ff",
        data={CONF_BLE_DEVICES: {_MOWER: "aa:bb:cc:dd:ee:ff"}},
    )
    entry.add_to_hass(hass)
    await async_get_store(hass, entry).async_load_device_data()
    manager = create_autospec(MammotionClient, instance=True)
    manager.get_device_by_name.return_value = library_device
    manager.mower.return_value = None
    coordinator = coordinator_cls(
        hass, entry, _create_ble_only_device(_MOWER), manager, unique_name=_MOWER
    )
    await coordinator.async_restore_data()
    return coordinator


def _device(*, charge_limit: int = 80, rain_detection: bool = False) -> MowingDevice:
    device = MowingDevice()
    device.mower_state.charge_settings = ChargeSettings(
        smart_charge=charge_limit == 100, charge_limit=charge_limit
    )
    device.mower_state.rain_detection = rain_detection
    return device


async def _push(
    coordinator: MammotionBaseUpdateCoordinator[Any], device: MowingDevice
) -> None:
    await coordinator._on_state_changed(DeviceStateMachine("dev-1", device).current)  # noqa: SLF001


def _saved(coordinator: MammotionBaseUpdateCoordinator[Any]) -> dict[str, Any]:
    return coordinator._store.device_data[_MOWER]["mower_state"]  # noqa: SLF001


@pytest.mark.regression
async def test_a_pushed_charge_setting_is_what_a_restart_restores(
    hass: HomeAssistant,
) -> None:
    """The device reported smart on / 100, but a restart restored the older off / 80."""
    coordinator = await _coordinator(hass)

    await _push(coordinator, _device(charge_limit=100))

    assert _saved(coordinator)["charge_settings"]["charge_limit"] == 100
    assert _saved(coordinator)["charge_settings"]["smart_charge"] is True


@pytest.mark.regression
async def test_every_restored_setting_is_persisted_on_push(hass: HomeAssistant) -> None:
    """Not a charging special case: any field the entities show is saved."""
    coordinator = await _coordinator(hass)

    await _push(coordinator, _device(rain_detection=True))

    assert _saved(coordinator)["rain_detection"] is True


async def test_pushes_inside_the_interval_are_not_serialised_again(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A push every few seconds must not serialise the whole device every time."""
    coordinator = await _coordinator(hass)
    await _push(coordinator, _device(charge_limit=80))

    freezer.tick(STATE_PUSH_SAVE_INTERVAL - timedelta(seconds=1))
    await _push(coordinator, _device(charge_limit=90))
    assert _saved(coordinator)["charge_settings"]["charge_limit"] == 80

    freezer.tick(timedelta(seconds=1))
    await _push(coordinator, _device(charge_limit=95))
    assert _saved(coordinator)["charge_settings"]["charge_limit"] == 95


async def test_shutdown_saves_the_state_a_throttled_push_left_unsaved(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A graceful restart must not lose the change the throttle skipped."""
    coordinator = await _coordinator(hass)
    await _push(coordinator, _device(charge_limit=80))
    freezer.tick(timedelta(seconds=1))
    await _push(coordinator, _device(charge_limit=90))
    assert _saved(coordinator)["charge_settings"]["charge_limit"] == 80, (
        "the second push should have been throttled"
    )

    await coordinator.async_shutdown()

    assert _saved(coordinator)["charge_settings"]["charge_limit"] == 90


async def test_a_sibling_coordinator_does_not_save_the_shared_device(
    hass: HomeAssistant,
) -> None:
    """The map coordinator sees the same pushes under the same store key.

    Only the report coordinator owns that entry; a sibling saving too would
    serialise the whole device once more per push, and again at shutdown.
    """
    coordinator = await _coordinator(
        hass, MammotionMapUpdateCoordinator, library_device=_device(charge_limit=100)
    )

    await _push(coordinator, _device(charge_limit=100))
    assert _MOWER not in coordinator._store.device_data  # noqa: SLF001

    await coordinator.async_shutdown()
    assert _MOWER not in coordinator._store.device_data  # noqa: SLF001
