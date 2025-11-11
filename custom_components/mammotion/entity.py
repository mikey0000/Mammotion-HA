"""Base class for entities."""

from abc import ABC

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import callback
from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pymammotion.data.model.device import RTKDevice

from .const import CONF_RETRY_COUNT, DEFAULT_RETRY_COUNT, DOMAIN
from .coordinator import MammotionBaseUpdateCoordinator, MammotionRTKCoordinator


class MammotionBaseEntity(CoordinatorEntity[MammotionBaseUpdateCoordinator]):
    """Representation of a Mammotion Lawn Mower."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MammotionBaseUpdateCoordinator, key: str) -> None:
        """Initialize the Lawn Mower."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_name}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        mower = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        swversion = mower.state.device_firmwares.device_version

        model_id = None
        if mower is not None:
            if mower.state.mower_state.model_id != "":
                model_id = mower.state.mower_state.model_id
            if (
                mower.state.mqtt_properties is not None
                and mower.state.mqtt_properties.params.items.extMod is not None
            ):
                model_id = mower.state.mqtt_properties.params.items.extMod.value

        nick_name = self.coordinator.device.nick_name
        device_name = (
            self.coordinator.device_name
            if nick_name is None or nick_name == ""
            else self.coordinator.device.nick_name
        )

        connections: set[tuple[str, str]] = set()

        if mower.ble:
            connections.add(
                (
                    CONNECTION_BLUETOOTH,
                    format_mac(mower.ble.ble_device.address),
                )
            )
        elif mower.state.mower_state.ble_mac != "":
            connections.add(
                (
                    CONNECTION_BLUETOOTH,
                    format_mac(mower.state.mower_state.ble_mac),
                )
            )

        if mower.state.mower_state.wifi_mac != "":
            connections.add(
                (
                    CONNECTION_NETWORK_MAC,
                    format_mac(mower.state.mower_state.wifi_mac),
                )
            )

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_name)},
            manufacturer="Mammotion",
            serial_number=self.coordinator.device_name.split("-", 1)[-1],
            model_id=model_id,
            name=device_name,
            sw_version=swversion,
            model=self.coordinator.device.product_model or model_id,
            suggested_area="Garden",
            connections=connections,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # self._update_attr()
        super()._handle_coordinator_update()

    # @abstractmethod
    # @callback
    # def _update_attr(self) -> None:
    #     """Update the attribute of the entity."""

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.data is not None
            and self.coordinator.update_failures
            <= self.coordinator.config_entry.options.get(
                CONF_RETRY_COUNT, DEFAULT_RETRY_COUNT
            )
            and self.coordinator.is_online()
        )


class MammotionBaseRTKEntity(CoordinatorEntity[MammotionRTKCoordinator]):
    """Representation of a Mammotion RTK entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MammotionRTKCoordinator, key: str) -> None:
        """Initialize the Lawn Mower."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_name}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        rtk_device: RTKDevice = self.coordinator.data

        return DeviceInfo(
            identifiers={(DOMAIN, rtk_device.name)},
            name=rtk_device.name
            if self.coordinator.device.nick_name is None
            else self.coordinator.device.nick_name,
            manufacturer="Mammotion",
            serial_number=rtk_device.name,
            model=rtk_device.name,
            model_id=self.coordinator.device.product_key,
            sw_version=self.coordinator.data.device_version,
            suggested_area="Garden",
            connections={
                (CONNECTION_BLUETOOTH, rtk_device.bt_mac),
                (CONNECTION_NETWORK_MAC, rtk_device.wifi_sta_mac),
            },
        )

    @property
    def available(self) -> bool:
        return self.coordinator.data.online

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()


class MammotionCameraBaseEntity(Camera, ABC):
    """Devices that support cameras."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_is_streaming = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: MammotionBaseUpdateCoordinator, key: str) -> None:
        """Initialize the camera."""
        super().__init__()
        # The API "name" field is a unique device identifier.
        self._attr_unique_id = f"{coordinator.device_name}_{key}"
