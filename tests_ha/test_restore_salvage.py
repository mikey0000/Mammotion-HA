"""A stored device whose state no longer decodes keeps whatever still does.

Restoring used to swap the whole device for an empty ``MowingDevice()`` on the
first undecodable field, silently: one stale sub-model then cost the map, the
plans and the firmware, and every firmware-gated entity failed closed.  The real
report coordinator restores here from the real entry store.
"""

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import MowingDevice, PoolCleanerDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion import coordinator as coordinator_module
from custom_components.mammotion.config import (
    STORE_MINOR_VERSION,
    STORE_VERSION,
    async_get_store,
)
from custom_components.mammotion.const import CONF_BLE_DEVICES, DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_MOWER = "Luba-VS123456"
_ENTRY_ID = "entry-1"
_STORE_KEY = f"{DOMAIN}.{_ENTRY_ID}"
_FIRMWARE = "2.3.30.39"
_LOGGER_NAME = "custom_components.mammotion"


def _stored_device(**work: Any) -> dict[str, Any]:
    """Return a saved mower whose ``work`` section carries *work*."""
    device = MowingDevice()
    device.device_firmwares.device_version = _FIRMWARE
    data = device.to_dict()
    data["work"].update(work)
    return data


async def _coordinator(
    hass: HomeAssistant,
) -> tuple[MammotionReportUpdateCoordinator, MagicMock]:
    """Build the real report coordinator over the loaded entry store."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=_ENTRY_ID,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={CONF_BLE_DEVICES: {_MOWER: "aa:bb:cc:dd:ee:ff"}},
    )
    entry.add_to_hass(hass)
    await async_get_store(hass, entry).async_load_device_data()
    handle = MagicMock()
    manager = MagicMock()
    manager.mower = MagicMock(return_value=handle)
    coordinator = MammotionReportUpdateCoordinator(
        hass, entry, _create_ble_only_device(_MOWER), manager, unique_name=_MOWER
    )
    return coordinator, handle


async def _restore(
    hass: HomeAssistant, hass_storage: dict[str, Any], stored: Any
) -> tuple[MammotionReportUpdateCoordinator, MagicMock]:
    """Restore the real report coordinator from a store holding *stored*."""
    hass_storage[_STORE_KEY] = {
        "version": STORE_VERSION,
        "minor_version": STORE_MINOR_VERSION,
        "key": _STORE_KEY,
        "data": {
            "devices": {_MOWER: stored},
            "transports": {},
            "firmware_checks": {},
            "capabilities": {},
        },
    }
    coordinator, handle = await _coordinator(hass)
    await coordinator.async_restore_data()
    return coordinator, handle


def test_the_bad_input_really_fails_to_decode() -> None:
    """Guards the fixture: a decoder that learns to accept it would void these tests."""
    with pytest.raises(ValueError, match="speed"):
        MowingDevice.from_dict(_stored_device(speed="not a number"))


async def test_one_stale_sub_model_does_not_cost_the_firmware(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Only ``work`` is dropped; firmware, which gates entities, survives."""
    coordinator, handle = await _restore(
        hass, hass_storage, _stored_device(speed="not a number")
    )

    assert coordinator.data.device_firmwares.device_version == _FIRMWARE
    assert coordinator.data.work == MowingDevice().work
    handle.restore_device.assert_called_once_with(coordinator.data)


async def test_the_dropped_field_is_logged_by_path(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The user has to be able to tell what was lost, and why."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await _restore(hass, hass_storage, _stored_device(speed="not a number"))

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(_MOWER in m and "work.speed" in m for m in warnings), warnings


async def test_an_undecodable_store_falls_back_to_empty_and_says_so(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Something that is not a device at all still restores, but not silently."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        coordinator, handle = await _restore(hass, hass_storage, ["not", "a", "dict"])

    assert coordinator.data == MowingDevice()
    handle.restore_device.assert_called_once_with(coordinator.data)
    assert any(
        _MOWER in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
    )


async def test_a_device_with_nothing_stored_restores_empty_quietly(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A first run has nothing to lose, so nothing is worth a warning."""
    coordinator, handle = await _coordinator(hass)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await coordinator.async_restore_data()

    assert coordinator.data == MowingDevice()
    handle.restore_device.assert_called_once_with(coordinator.data)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_pool_cleaner_restore_salvages_the_same_way() -> None:
    """The RTK and pool coordinators share the one helper."""
    device = PoolCleanerDevice()
    device.device_firmwares.device_version = _FIRMWARE
    stored = device.to_dict()
    stored["pool_state"] = 5

    restored = coordinator_module.restore_device_state(
        PoolCleanerDevice, stored, "Spino-S1234"
    )

    assert restored.device_firmwares.device_version == _FIRMWARE
    assert restored.pool_state == PoolCleanerDevice().pool_state
