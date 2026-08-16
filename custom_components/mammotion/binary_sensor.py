"""Mammotion binary sensor entities."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.device import MowingDevice, PoolCleanerDevice

from . import MammotionConfigEntry
from .coordinator import MammotionBaseUpdateCoordinator, MammotionSpinoCoordinator
from .entity import MammotionBaseEntity, MammotionBaseSpinoEntity


@dataclass(frozen=True, kw_only=True)
class MammotionBinarySensorEntityDescription(
    BinarySensorEntityDescription,
):
    """Describes Mammotion binary sensor entity."""

    is_on_fn: Callable[[MowingDevice], bool | None]


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoBinarySensorEntityDescription(
    BinarySensorEntityDescription,
):
    """Describes a Mammotion Spino pool cleaner binary sensor entity."""

    is_on_fn: Callable[[PoolCleanerDevice], bool | None]


BINARY_SENSORS: tuple[MammotionBinarySensorEntityDescription, ...] = (
    MammotionBinarySensorEntityDescription(
        key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda mower_data: mower_data.report_data.dev.charge_state in (1, 2),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

SPINO_BINARY_SENSORS: tuple[MammotionSpinoBinarySensorEntityDescription, ...] = (
    MammotionSpinoBinarySensorEntityDescription(
        key="spino_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda spino_data: spino_data.pool_state.charging,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion sensor entity."""
    mammotion_devices = entry.runtime_data.mowers

    for mower in mammotion_devices:
        async_add_entities(
            MammotionBinarySensorEntity(mower.reporting_coordinator, entity_description)
            for entity_description in BINARY_SENSORS
        )

    for spino in entry.runtime_data.spino:
        async_add_entities(
            MammotionSpinoBinarySensorEntity(spino.coordinator, entity_description)
            for entity_description in SPINO_BINARY_SENSORS
        )


class MammotionBinarySensorEntity(MammotionBaseEntity, BinarySensorEntity):
    """Mammotion sensor entity."""

    entity_description: MammotionBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.translation_key

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self.entity_description.is_on_fn(self.coordinator.data)


class MammotionSpinoBinarySensorEntity(MammotionBaseSpinoEntity, BinarySensorEntity):
    """Mammotion Spino pool cleaner binary sensor entity."""

    entity_description: MammotionSpinoBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: MammotionSpinoCoordinator,
        entity_description: MammotionSpinoBinarySensorEntityDescription,
    ) -> None:
        """Initialize the Spino binary sensor entity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self.entity_description.is_on_fn(self.coordinator.data)
