"""Firmware update functionality for the Mammotion integration."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.update import UpdateEntity, UpdateEntityDescription
from homeassistant.const import EntityCategory

from .coordinator import MammotionDataUpdateCoordinator
from .entity import MammotionBaseEntity

class MammotionFirmwareUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator to manage firmware updates for Mammotion devices."""

    def __init__(self, hass: HomeAssistant, coordinator: MammotionDataUpdateCoordinator):
        """Initialize the firmware update coordinator."""
        super().__init__(hass, coordinator.logger, name="Mammotion Firmware Update")
        self.coordinator = coordinator

    async def async_check_for_updates(self):
        """Check for firmware updates."""
        await self.coordinator.async_send_command("check_firmware_update")

    async def async_download_update(self):
        """Download firmware update."""
        await self.coordinator.async_send_command("download_firmware_update")

    async def async_install_update(self):
        """Install firmware update."""
        await self.coordinator.async_send_command("install_firmware_update")

    async def async_update(self):
        """Update the firmware."""
        await self.async_check_for_updates()
        await self.async_download_update()
        await self.async_install_update()

class MammotionFirmwareUpdateEntity(MammotionBaseEntity, UpdateEntity):
    """Representation of a firmware update entity for Mammotion devices."""

    def __init__(self, coordinator: MammotionDataUpdateCoordinator):
        """Initialize the firmware update entity."""
        super().__init__(coordinator, "firmware_update")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_name = "Firmware Update"
        self._attr_unique_id = f"{coordinator.device_name}_firmware_update"
        self._attr_should_poll = False

    async def async_update(self):
        """Update the firmware entity."""
        await self.coordinator.async_update()

    async def async_check_for_updates(self):
        """Check for firmware updates."""
        await self.coordinator.async_check_for_updates()

    async def async_download_update(self):
        """Download firmware update."""
        await self.coordinator.async_download_update()

    async def async_install_update(self):
        """Install firmware update."""
        await self.coordinator.async_install_update()
