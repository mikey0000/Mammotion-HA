"""Luba lawn mowers."""

from __future__ import annotations

import logging

from pyluba.mammotion.devices.luba import has_field
from pyluba.utility.constant.device_constant import WorkMode

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MammotionConfigEntry
from .coordinator import MammotionDataUpdateCoordinator
from .entity import MammotionBaseEntity

SUPPORTED_FEATURES = (
    LawnMowerEntityFeature.DOCK
    | LawnMowerEntityFeature.PAUSE
    | LawnMowerEntityFeature.START_MOWING
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Luba config entry."""
    coordinator = entry.runtime_data
    async_add_entities([MammotionLawnMowerEntity(coordinator)])


class MammotionLawnMowerEntity(MammotionBaseEntity, LawnMowerEntity):
    """Representation of a Mammotion lawn mower."""

    _attr_supported_features = SUPPORTED_FEATURES
    _attr_activity = None

    def __init__(self, coordinator: MammotionDataUpdateCoordinator) -> None:
        """Initialize the lawn mower."""
        super().__init__(coordinator, "mower")
        self._attr_name = None  # main feature of device

    def _get_mower_activity(self) -> LawnMowerActivity | None:
        mode = 0
        charge_state = 0
        if has_field(self.mower_data.sys.toapp_report_data.dev):
            mode = self.mower_data.sys.toapp_report_data.dev.sys_status
            charge_state = self.mower_data.sys.toapp_report_data.dev.charge_state
        _LOGGER.debug("activity mode %s", mode)
        if (
            mode == WorkMode.MODE_PAUSE
            or mode == WorkMode.MODE_READY
            and charge_state == 0
        ):
            return LawnMowerActivity.PAUSED
        if mode in (WorkMode.MODE_WORKING, WorkMode.MODE_RETURNING):
            return LawnMowerActivity.MOWING
        if mode == WorkMode.MODE_LOCK:
            return LawnMowerActivity.ERROR
        if mode == WorkMode.MODE_READY and charge_state != 0:
            return LawnMowerActivity.DOCKED

        return self._attr_activity

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return the state of the mower."""
        return self._get_mower_activity()

    async def async_start_mowing(self) -> None:
        """Start mowing."""
        # check if job in progress
        #
        if has_field(self.coordinator.device.luba_msg.sys.toapp_report_data.dev):
            dev = self.coordinator.device.luba_msg.sys.toapp_report_data.dev
            if dev.sys_status == WorkMode.MODE_PAUSE:
                return await self.coordinator.device.command("resume_execute_task")
        await self.coordinator.device.command("start_work_job")

    async def async_dock(self) -> None:
        """Start docking."""
        await self.coordinator.device.command("return_to_dock")

    async def async_pause(self) -> None:
        """Pause mower."""
        await self.coordinator.device.command("pause_execute_task")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(self.coordinator.device.raw_data)
        self._attr_activity = self._get_mower_activity()
        self.async_write_ha_state()
