"""Shared scaffolding for the battery-settings tests.

``make_mower`` wraps a real ``MowingDevice`` in the real ``MammotionMowerData`` the
platform setups walk, with spec'd coordinators and client (``MagicMock(spec=…)``:
autospeccing a coordinator costs ~80 ms, and a spec still makes its coroutine
methods ``AsyncMock``).  ``make_report_coordinator``
is the real report coordinator over the loaded entry store, with a spec'd client
and no transport handle, for tests that drive its send paths.
"""

from collections.abc import Iterable
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, create_autospec

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.config import async_get_store
from custom_components.mammotion.const import CONF_BLE_DEVICES, DOMAIN
from custom_components.mammotion.coordinator import (
    MammotionDeviceErrorUpdateCoordinator,
    MammotionDeviceVersionUpdateCoordinator,
    MammotionMaintenanceUpdateCoordinator,
    MammotionMapUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)
from custom_components.mammotion.models import MammotionDevices, MammotionMowerData
from custom_components.mammotion.notifications import MowerNotifier


def make_mower(name: str, firmware: str = "") -> MammotionMowerData:
    """Return the mower record the platform setups walk, over a real ``MowingDevice``."""
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    api = MagicMock(spec=MammotionClient)
    api.get_device_by_name.return_value = None
    coordinator = MagicMock(spec=MammotionReportUpdateCoordinator)
    coordinator.data = device
    coordinator.device_name = name
    coordinator.unique_name = name
    coordinator.operation_settings = OperationSettings()
    coordinator.manager = api
    coordinator.map_offset_lat = coordinator.map_offset_lon = 0.0
    return MammotionMowerData(
        name=name,
        unique_name=name,
        api=api,
        maintenance_coordinator=MagicMock(spec=MammotionMaintenanceUpdateCoordinator),
        reporting_coordinator=coordinator,
        version_coordinator=MagicMock(spec=MammotionDeviceVersionUpdateCoordinator),
        map_coordinator=MagicMock(spec=MammotionMapUpdateCoordinator),
        error_coordinator=MagicMock(spec=MammotionDeviceErrorUpdateCoordinator),
        notifier=MagicMock(spec=MowerNotifier),
        device=_create_ble_only_device(name),
    )


async def platform_entities(
    platform: ModuleType,
    mower: MammotionMowerData,
    hass: HomeAssistant | None = None,
) -> dict[str, Any]:
    """Run the platform's own setup and return what it created, keyed by entity key.

    Pass a real *hass* for a platform whose setup touches the entity registry.
    """
    entry = create_autospec(ConfigEntry, instance=True)
    entry.runtime_data = MammotionDevices(mowers=[mower], RTK=[], spino=[])
    added: list[Entity] = []

    def _add_entities(
        new_entities: Iterable[Entity], update_before_add: bool = False
    ) -> None:
        added.extend(new_entities)

    await platform.async_setup_entry(
        hass or create_autospec(HomeAssistant, instance=True), entry, _add_entities
    )
    return {entity.entity_description.key: entity for entity in added}


async def make_report_coordinator(
    hass: HomeAssistant, name: str, firmware: str
) -> MammotionReportUpdateCoordinator:
    """Build the real report coordinator with a spec'd client and no transport handle."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-1",
        unique_id="aa:bb:cc:dd:ee:ff",
        data={CONF_BLE_DEVICES: {name: "aa:bb:cc:dd:ee:ff"}},
    )
    entry.add_to_hass(hass)
    await async_get_store(hass, entry).async_load_device_data()
    device = MowingDevice()
    device.online = True
    device.device_firmwares.device_version = firmware
    manager = create_autospec(MammotionClient, instance=True)
    manager.get_device_by_name.return_value = device
    manager.mower.return_value = None
    coordinator = MammotionReportUpdateCoordinator(
        hass, entry, _create_ble_only_device(name), manager, unique_name=name
    )
    coordinator.data = device
    return coordinator


def sent_commands(coordinator: MammotionReportUpdateCoordinator) -> list[str]:
    """Return the name of every command the coordinator handed the client, in order."""
    return [
        call.args[1]
        for call in coordinator.manager.send_command_and_wait.await_args_list
    ]
