"""Firmware-gated entities appear when the firmware becomes known, exactly once.

Platforms set up once, often before the mower has reported its firmware (a first
run, or a restore that lost it).  A gate evaluated only then fails closed for
good: the Luba 3's Smart charging switch vanished that way.  The real switch and
number platforms run here over a real ``DataUpdateCoordinator``, and firmware
arrives through ``async_set_updated_data`` as it does at runtime.
"""

import copy
import logging
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import number as number_platform
from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.const import DOMAIN

_GATED = {"smart_charge", "charge_limit", "auto_change_direction"}
_SUPPORTED = "2.3.30.39"


class _Platforms:
    """The switch and number platforms of one mower, recording every entity added."""

    def __init__(self, hass: HomeAssistant, name: str) -> None:
        """Build the mower record the setups walk, with real coordinator listeners."""
        # Entity constructors read many coordinator attributes, so the coordinator is
        # the stand-in the other gate tests use; listening and pushing are the real
        # DataUpdateCoordinator's.
        self._listeners = DataUpdateCoordinator(
            hass,
            logging.getLogger(__name__),
            config_entry=None,
            name=name,
            update_interval=None,
        )
        coordinator = MagicMock()
        coordinator.data = MowingDevice()
        coordinator.device_name = name
        coordinator.unique_name = name
        coordinator.operation_settings = OperationSettings()
        coordinator.async_add_listener = self._listeners.async_add_listener
        mower = MagicMock()
        mower.name = name
        mower.device.device_name = name
        mower.device.product_key = ""
        mower.device.iot_id = "iot-id"
        mower.api.get_device_by_name.return_value = None
        mower.reporting_coordinator = coordinator
        self.coordinator = coordinator
        self.entry = MockConfigEntry(domain=DOMAIN)
        self.entry.runtime_data = MagicMock(mowers=[mower], spino=[], RTK=[])
        self.added: list[str] = []

    def _add(self, entities: Any) -> None:
        self.added.extend(entity.entity_description.key for entity in entities)

    async def async_setup(self, hass: HomeAssistant) -> None:
        """Run both platforms' own setup."""
        platform: ModuleType
        for platform in (switch_platform, number_platform):
            await platform.async_setup_entry(hass, self.entry, self._add)

    def push_firmware(self, firmware: str) -> None:
        """Deliver a new device snapshot carrying *firmware*, as the state bus does."""
        device = copy.deepcopy(self.coordinator.data)
        device.device_firmwares.device_version = firmware
        self.coordinator.data = device
        self._listeners.async_set_updated_data(device)

    def gated(self) -> list[str]:
        """Return every gated key added so far, repeats included."""
        return sorted(key for key in self.added if key in _GATED)


async def test_unknown_firmware_creates_none_of_them(hass: HomeAssistant) -> None:
    """Every one of these writes device state, so an unread version stays closed."""
    platforms = _Platforms(hass, "Luba-VA123456")
    await platforms.async_setup(hass)

    assert platforms.gated() == []


async def test_they_appear_once_when_firmware_arrives(hass: HomeAssistant) -> None:
    """A later snapshot opens the gate; a repeated one must not add them again."""
    platforms = _Platforms(hass, "Luba-VA123456")
    await platforms.async_setup(hass)

    platforms.push_firmware(_SUPPORTED)
    assert platforms.gated() == sorted(_GATED)

    platforms.push_firmware(_SUPPORTED)
    assert platforms.gated() == sorted(_GATED)


async def test_firmware_known_at_setup_adds_them_once(hass: HomeAssistant) -> None:
    """Setup adds them straight away, and the listener does not add them again."""
    platforms = _Platforms(hass, "Luba-VA123456")
    platforms.coordinator.data.device_firmwares.device_version = _SUPPORTED
    await platforms.async_setup(hass)
    assert platforms.gated() == sorted(_GATED)

    platforms.push_firmware(_SUPPORTED)
    assert platforms.gated() == sorted(_GATED)


@pytest.mark.parametrize(
    ("name", "firmware"),
    [("Luba-VA123456", "2.0.0.0"), ("Spino-S1123456", _SUPPORTED)],
)
async def test_an_unsupported_device_never_gets_them(
    hass: HomeAssistant, name: str, firmware: str
) -> None:
    """Old firmware, or a pool robot, stays closed however often it reports."""
    platforms = _Platforms(hass, name)
    await platforms.async_setup(hass)

    platforms.push_firmware(firmware)
    platforms.push_firmware(firmware)

    assert platforms.gated() == []


async def test_unloading_the_entry_stops_listening(hass: HomeAssistant) -> None:
    """A reload sets the platforms up afresh; the old listener must not add too."""
    platforms = _Platforms(hass, "Luba-VA123456")
    await platforms.async_setup(hass)

    await platforms.entry._async_process_on_unload(hass)  # noqa: SLF001
    platforms.push_firmware(_SUPPORTED)

    assert platforms.gated() == []
