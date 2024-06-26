import logging

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyluba.proto import luba_msg

from .const import DOMAIN
from .coordinator import MammotionDataUpdateCoordinator
from pyluba.utility.device_type import DeviceType

_LOGGER = logging.getLogger(__name__)


class MammotionBaseEntity(CoordinatorEntity[MammotionDataUpdateCoordinator]):
    """Representation of a Luba lawn mower."""

    _attr_has_entity_name = True

    def __init__(
        self, device_name: str, coordinator: MammotionDataUpdateCoordinator
    ) -> None:
        """Initialize the lawn mower."""
        super().__init__(coordinator)
        self._attr_name = device_name
        self._attr_unique_id = f"{device_name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_name)},
            manufacturer="Mammotion",
            serial_number=device_name.split('-', 1)[-1],
            # sw_version=coordinator.device.luba_msg.net.toapp_devinfo_resp.resp_ids.get(0, {}).get('info', "Loading..."),
            # sw_version=coordinator.device.luba_msg.net.toapp_devinfo_resp.resp_ids[0].info if coordinator.device.luba_msg.net.toapp_devinfo_resp.resp_ids else "Loading...",
            model=DeviceType.value_of_str(device_name, coordinator.device.luba_msg.net.toapp_wifi_iot_status.productkey).get_name(),
            name=device_name,
            suggested_area="Garden",
        )

    @property
    def mower_data(self) -> luba_msg:
        return self.coordinator.device.luba_msg
