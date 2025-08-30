"""Update entity for Mammotion."""

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MammotionBaseUpdateCoordinator
from .entity import MammotionBaseEntity

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MammotionUpdateEntityDescription(UpdateEntityDescription):
    """Describes Mammotion switch entity."""

    key: str


MammotionUpdate = MammotionUpdateEntityDescription(
    key="update",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up update entities for Netgear component."""
    mammotion_devices = entry.runtime_data
    entities = []
    for mower in mammotion_devices:
        entity = MammotionUpdateEntity(mower.version_coordinator, MammotionUpdate)
        entities.append(entity)

    async_add_entities(entities)


class MammotionUpdateEntity(MammotionBaseEntity, UpdateEntity):
    """Update entity for a Netgear device."""

    entity_description: MammotionUpdateEntityDescription

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.RELEASE_NOTES
        | UpdateEntityFeature.PROGRESS
    )
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionBaseUpdateCoordinator,
        entity_description: MammotionUpdateEntityDescription,
    ) -> None:
        """Initialize a Netgear device."""
        super().__init__(coordinator, entity_description.key)
        self.coordinator = coordinator
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def installed_version(self) -> str | None:
        """Version currently in use."""
        if self.coordinator.data is not None:
            return self.coordinator.data.device_firmwares.device_version
        return None

    @property
    def latest_version(self) -> str | None:
        """Latest version available for install."""
        if (
            self.coordinator.data.update_check.upgradeable
            and self.coordinator.data.update_check.product_version_info_vo is not None
        ):
            new_version = self.coordinator.data.update_check.product_version_info_vo
            return new_version.release_version
        return self.installed_version

    @property
    def release_summary(self) -> str | None:
        """Release summary."""
        if self.coordinator.data.update_check.product_version_info_vo is not None:
            return (
                self.coordinator.data.update_check.product_version_info_vo.release_note
            )
        return None

    def release_notes(self) -> str | None:
        """Release notes."""
        if self.coordinator.data.update_check.product_version_info_vo is not None:
            return (
                self.coordinator.data.update_check.product_version_info_vo.release_note
            )
        return None

    @property
    def in_progress(self) -> bool:
        """Update installation in progress."""
        return self.coordinator.data.update_check.isupgrading

    @property
    def update_percentage(self) -> int | float | None:
        """Update installation progress percentage."""
        if self.coordinator.data.update_check.isupgrading:
            return self.coordinator.data.update_check.progress
        return None

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install the latest firmware version."""
        if version is None:
            version = self.latest_version
        if version:
            await self.coordinator.update_firmware(version)
        await self.coordinator.async_refresh()

    @callback
    def async_update_device(self) -> None:
        """Update the Mammotion device."""
