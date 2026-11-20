"""Support for Mammotion time entities."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import time
from functools import partial

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.device_info import ChargeSettings
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .coordinator import MammotionReportUpdateCoordinator
from .entity import MammotionBaseEntity, async_add_when_firmware_supports


@dataclass(frozen=True, kw_only=True)
class MammotionTimeEntityDescription(TimeEntityDescription):  # type: ignore[misc]
    """Describes a Mammotion time entity; the device counts minutes since midnight."""

    minutes_fn: Callable[[ChargeSettings], int]
    set_fn: Callable[[MammotionReportUpdateCoordinator, int], Awaitable[None]]


# Gated per device on DeviceType.supports_charge_limit, as the rest of the battery page.
OFF_PEAK_TIME_ENTITIES: tuple[MammotionTimeEntityDescription, ...] = (
    MammotionTimeEntityDescription(
        key="off_peak_charging_start",
        minutes_fn=lambda settings: settings.valley_charge_start_time,
        set_fn=lambda coordinator, minutes: coordinator.async_set_off_peak_start(
            minutes
        ),
        entity_category=EntityCategory.CONFIG,
    ),
    MammotionTimeEntityDescription(
        key="off_peak_charging_end",
        minutes_fn=lambda settings: settings.valley_charge_end_time,
        set_fn=lambda coordinator, minutes: coordinator.async_set_off_peak_end(minutes),
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion time entities."""
    for mower in entry.runtime_data.mowers:
        coordinator = mower.reporting_coordinator
        async_add_when_firmware_supports(
            entry,
            coordinator,
            supported=partial(
                DeviceType.supports_charge_limit, mower.device.device_name
            ),
            descriptions=OFF_PEAK_TIME_ENTITIES,
            build=partial(MammotionTimeEntity, coordinator),
            async_add_entities=async_add_entities,
        )


class MammotionTimeEntity(MammotionBaseEntity, TimeEntity):  # type: ignore[misc]
    """A time of day stored on the mower."""

    entity_description: MammotionTimeEntityDescription

    def __init__(
        self,
        coordinator: MammotionReportUpdateCoordinator,
        entity_description: MammotionTimeEntityDescription,
    ) -> None:
        """Initialize the time entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def native_value(self) -> time | None:
        """Return the stored time, or None until the mower has reported its settings."""
        settings = self.coordinator.data.mower_state.charge_settings
        if not settings.reported:
            return None
        return time(*divmod(self.entity_description.minutes_fn(settings), 60))

    async def async_set_value(self, value: time) -> None:
        """Store *value* on the mower."""
        await self.entity_description.set_fn(
            self.coordinator, value.hour * 60 + value.minute
        )
