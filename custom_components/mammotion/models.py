from dataclasses import dataclass

from homeassistant.helpers.device_registry import DeviceInfo
from pymammotion.mammotion.devices.mammotion import Mammotion

from . import (
    MammotionDeviceVersionUpdateCoordinator,
    MammotionMaintenanceUpdateCoordinator,
)
from .coordinator import MammotionReportUpdateCoordinator


@dataclass
class MammotionMowerData:
    """Data for a mower information."""

    name: str
    api: Mammotion
    maintenance_coordinator: MammotionMaintenanceUpdateCoordinator
    reporting_coordinator: MammotionReportUpdateCoordinator
    version_coordinator: MammotionDeviceVersionUpdateCoordinator
    # maps_coordinator:
    device: DeviceInfo


@dataclass
class MammotionDevices:
    """Data for the Mammotion integration."""

    mowers: list[MammotionMowerData]
