"""Base class for entities."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_RETRY_COUNT, DOMAIN
from .coordinator import MammotionDataUpdateCoordinator


class MammotionBaseEntity(CoordinatorEntity[MammotionDataUpdateCoordinator]):
    """Representation of a Luba lawn mower."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MammotionDataUpdateCoordinator, key: str) -> None:
        """Initialize the lawn mower."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_name}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.device_name)},
            manufacturer="Mammotion",
            serial_number=coordinator.device.luba_msg.net.toapp_wifi_iot_status.productkey,
            name=coordinator.device_name,
            suggested_area="Garden",
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.data is not None
            and self.coordinator.update_failures
            < self.coordinator.config_entry.options[CONF_RETRY_COUNT]
        )
