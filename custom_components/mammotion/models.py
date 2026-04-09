"""models"""

from dataclasses import dataclass

from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.client import MammotionClient

from .coordinator import (
    MammotionDeviceErrorUpdateCoordinator,
    MammotionDeviceVersionUpdateCoordinator,
    MammotionMaintenanceUpdateCoordinator,
    MammotionMapUpdateCoordinator,
    MammotionReportUpdateCoordinator,
    MammotionRTKCoordinator,
)


@dataclass
class MammotionMowerData:
    """Data for a mower information."""

    name: str
    unique_name: str
    api: MammotionClient
    maintenance_coordinator: MammotionMaintenanceUpdateCoordinator
    reporting_coordinator: MammotionReportUpdateCoordinator
    version_coordinator: MammotionDeviceVersionUpdateCoordinator
    map_coordinator: MammotionMapUpdateCoordinator
    error_coordinator: MammotionDeviceErrorUpdateCoordinator
    device: Device


@dataclass
class MammotionRTKData:
    """Data for RTK information."""

    name: str
    unique_name: str
    api: MammotionClient
    coordinator: MammotionRTKCoordinator
    device: Device


@dataclass
class MammotionDevices:
    """Data for the Mammotion integration."""

    mowers: list[MammotionMowerData]
    RTK: list[MammotionRTKData]
    # TODO add spino
