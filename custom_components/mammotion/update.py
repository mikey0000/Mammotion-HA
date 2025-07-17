import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateDeviceClass, UpdateEntityFeature, \
    UpdateEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator
from custom_components.mammotion.entity import MammotionBaseEntity

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
        entity = MammotionUpdateEntity(mower.update_coordinator, MammotionUpdate)
        entities.append(entity)

    async_add_entities(entities)


class MammotionUpdateEntity(MammotionBaseEntity, UpdateEntity):
    """Update entity for a Netgear device."""
    entity_description: MammotionUpdateEntityDescription

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL
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
            return self.coordinator.data.mower_state.update_check
        return None

    @property
    def latest_version(self) -> str | None:
        """Latest version available for install."""
        if self.coordinator.data is not None:
            new_version = self.coordinator.data.get("NewVersion")
            if new_version is not None and not new_version.startswith(
                self.installed_version
            ):
                return new_version
        return self.installed_version

    @property
    def release_summary(self) -> str | None:
        """Release summary."""
        if self.coordinator.data is not None:
            self.coordinator.data.update_check.firmware_lastest_versions
            return .
        return None

    async def async_install(
            self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install the latest firmware version."""
        await self.coordinator.update_firmware(version)


    @callback
    def async_update_device(self) -> None:
        """Update the Mammotion device."""
