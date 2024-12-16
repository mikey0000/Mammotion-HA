from dataclasses import dataclass

from homeassistant.helpers.device_registry import DeviceInfo
from pymammotion.mammotion.devices.mammotion import Mammotion

from .coordinator import MammotionReportUpdateCoordinator


@dataclass
class MammotionMowerData:
    """Data for a mower information."""

    name: str
    api: Mammotion
    # maintenance_coordinator:
    reporting_coordinator: MammotionReportUpdateCoordinator
    # maps_coordinator:
    device: DeviceInfo


@dataclass
class MammotionDeviceData:
    """Data for the Mammotion integration."""

    mowers: list[MammotionMowerData]
