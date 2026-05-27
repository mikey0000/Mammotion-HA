"""Vacuum (pool cleaner) platform for the Mammotion integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.pool_state import SpinoSysStatus, SpinoWorkMode

from . import MammotionConfigEntry
from .coordinator import MammotionSpinoCoordinator
from .entity import MammotionBaseSpinoEntity

# The fan-speed picker maps to the Spino cleaning work modes. RECHARGE (the
# dock action exposed via return_to_base) and UNKNOWN are excluded — neither is
# a selectable cleaning speed.
_FAN_SPEED_EXCLUDED = {SpinoWorkMode.RECHARGE, SpinoWorkMode.UNKNOWN}
FAN_SPEED_MODES = [
    mode.name for mode in SpinoWorkMode if mode not in _FAN_SPEED_EXCLUDED
]

# dev_statue_t.sys_status (0-8) collapsed to a HA vacuum activity, following the
# app's STANDBY / WORKING / RETURNING bucketing in updateDeviceState().
ACTIVITY_MAP = {
    SpinoSysStatus.IDLE: VacuumActivity.IDLE,
    SpinoSysStatus.PREPARE: VacuumActivity.IDLE,
    SpinoSysStatus.WAIT_WATER: VacuumActivity.CLEANING,
    SpinoSysStatus.WORKING: VacuumActivity.CLEANING,
    SpinoSysStatus.PAUSE_GO_CHARGE: VacuumActivity.RETURNING,
    SpinoSysStatus.END_GO_CHARGE: VacuumActivity.RETURNING,
    SpinoSysStatus.CHARGING: VacuumActivity.DOCKED,
    SpinoSysStatus.LEAVE_DOCK: VacuumActivity.CLEANING,
    SpinoSysStatus.RECALLING: VacuumActivity.RETURNING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Spino pool cleaner vacuum entity."""
    entities = [
        MammotionSpinoVacuumEntity(spino.coordinator)
        for spino in entry.runtime_data.spino
    ]
    async_add_entities(entities)


class MammotionSpinoVacuumEntity(MammotionBaseSpinoEntity, StateVacuumEntity):
    """Spino pool cleaner represented as a vacuum entity."""

    _attr_name = None
    _attr_supported_features = (
        VacuumEntityFeature.START
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.STATE
    )
    _attr_fan_speed_list = FAN_SPEED_MODES

    def __init__(self, coordinator: MammotionSpinoCoordinator) -> None:
        """Initialize the pool cleaner vacuum entity."""
        super().__init__(coordinator, "vacuum")

    @property
    def activity(self) -> VacuumActivity:
        """Return the current cleaning activity."""
        return ACTIVITY_MAP.get(
            self.coordinator.data.pool_state.sys_status, VacuumActivity.IDLE
        )

    @property
    def fan_speed(self) -> str:
        """Return the current cleaning work mode."""
        return self.coordinator.data.pool_state.work_mode.name

    async def async_start(self) -> None:
        """Start cleaning in AUTO mode."""
        await self.coordinator.async_set_work_mode(SpinoWorkMode.AUTO.value)

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Send the cleaner back to recharge."""
        await self.coordinator.async_set_work_mode(SpinoWorkMode.RECHARGE.value)

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Switch the cleaning work mode."""
        await self.coordinator.async_set_work_mode(SpinoWorkMode[fan_speed].value)
