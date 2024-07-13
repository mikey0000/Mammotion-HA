"""Base class for entities."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pymammotion.utility.device_type import DeviceType

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
            serial_number=coordinator.device_name.split("-", 1)[-1],
            name=coordinator.device_name,
            # ToDo: To add in once betterproto is fixed
            # sw_version=coordinator.device.luba_msg.net.toapp_devinfo_resp.resp_ids.get(0, {}).get('info', "Loading..."),
            sw_version=coordinator.device.raw_data.get("net", {})
            .get("toapp_devinfo_resp", {})
            .get("resp_ids", [{}])[0]
            .get(
                "info", "Loading..."
            ),  # raw_data is a temp workaround until betterproto is fixed
            model=DeviceType.value_of_str(
                coordinator.device_name,
                coordinator.device.luba_msg.net.toapp_wifi_iot_status.productkey,
            ).get_model(),
            suggested_area="Garden",
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.data is not None
            and self.coordinator.update_failures
            <= self.coordinator.config_entry.options[CONF_RETRY_COUNT]
        )
