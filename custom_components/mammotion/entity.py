"""Base class for entities."""

from abc import ABC

from homeassistant.components import bluetooth
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import callback
from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.device_registry import (
    async_get as async_get_device_registry,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pymammotion.data.model.device import RTKBaseStationDevice

from .const import DOMAIN
from .coordinator import MammotionBaseUpdateCoordinator, MammotionRTKCoordinator


class MammotionBaseEntity(CoordinatorEntity[MammotionBaseUpdateCoordinator]):
    """Representation of a Mammotion Lawn Mower."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MammotionBaseUpdateCoordinator, key: str) -> None:
        """Initialize the Lawn Mower."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_name}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the HA device-registry info for this mower entity."""
        mower = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        swversion = mower.device_firmwares.device_version

        model_id = None
        if mower is not None:
            if mower.mower_state.model_id != "":
                model_id = mower.mower_state.model_id
            if (
                mower.mqtt_properties is not None
                and mower.mqtt_properties.params.items.extMod is not None
            ):
                model_id = mower.mqtt_properties.params.items.extMod.value

        connections: set[tuple[str, str]] = set()

        if mower.mower_state.ble_mac != "":
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, mower.mower_state.ble_mac.upper(), True
            )
            connections.add(
                (
                    CONNECTION_BLUETOOTH,
                    format_mac(
                        ble_device.address if ble_device else mower.mower_state.ble_mac
                    ),
                )
            )

        if mower.mower_state.wifi_mac != "":
            connections.add(
                (
                    CONNECTION_NETWORK_MAC,
                    format_mac(mower.mower_state.wifi_mac),
                )
            )

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.unique_name)},
            manufacturer="Mammotion",
            serial_number=self.coordinator.device_name.split("-", 1)[-1],
            model_id=model_id,
            name=self.coordinator.device_name,
            sw_version=swversion,
            model=self.coordinator.device.product_model or model_id,
            suggested_area="Garden",
            connections=connections,
        )

    async def async_added_to_hass(self) -> None:
        """Handle entity added to hass."""
        await super().async_added_to_hass()
        self._cleanup_stale_connections()

    @callback
    def _cleanup_stale_connections(self) -> None:
        """Replace device registry connections with only the valid mower state values."""
        mower = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        if mower is None:
            return

        device_registry = async_get_device_registry(self.hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, self.coordinator.unique_name)}
        )
        if device is None:
            return

        new_connections: set[tuple[str, str]] = set()
        if mower.mower_state.ble_mac != "":
            new_connections.add(
                (CONNECTION_BLUETOOTH, format_mac(mower.mower_state.ble_mac))
            )
        if mower.mower_state.wifi_mac != "":
            new_connections.add(
                (CONNECTION_NETWORK_MAC, format_mac(mower.mower_state.wifi_mac))
            )

        nick_name = self.coordinator.device.nick_name
        device_registry.async_update_device(
            device.id,
            new_connections=new_connections,
            name_by_user=nick_name if nick_name else None,
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
        return self.coordinator.data is not None and self.coordinator.is_online()


class MammotionBaseRTKEntity(CoordinatorEntity[MammotionRTKCoordinator]):
    """Representation of a Mammotion RTK entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MammotionRTKCoordinator, key: str) -> None:
        """Initialize the Lawn Mower."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_name}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the HA device-registry info for this RTK base station entity."""
        rtk_device: RTKBaseStationDevice = self.coordinator.data

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.unique_name)},
            name=self.coordinator.device_name,
            manufacturer="Mammotion",
            serial_number=self.coordinator.device_name,
            model=rtk_device.name,
            model_id=self.coordinator.device.product_key,
            sw_version=self.coordinator.data.device_version,
            suggested_area="Garden",
            connections={
                (CONNECTION_BLUETOOTH, rtk_device.bt_mac),
                (CONNECTION_NETWORK_MAC, rtk_device.wifi_mac),
            },
        )

    @property
    def available(self) -> bool:
        """Return True when the RTK base station reports itself online."""
        return self.coordinator.data.online

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Handle entity added to hass."""
        await super().async_added_to_hass()
        self._cleanup_stale_connections()

    @callback
    def _cleanup_stale_connections(self) -> None:
        """Replace device registry connections with only the valid mower state values."""
        rtk = self.coordinator.manager.get_device_by_name(self.coordinator.device_name)
        if rtk is None:
            return

        device_registry = async_get_device_registry(self.hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, self.coordinator.unique_name)}
        )
        if device is None:
            return

        new_connections: set[tuple[str, str]] = set()
        if rtk.bt_mac != "":
            new_connections.add((CONNECTION_BLUETOOTH, format_mac(rtk.bt_mac)))
        if rtk.wifi_mac != "":
            new_connections.add((CONNECTION_NETWORK_MAC, format_mac(rtk.wifi_mac)))

        nick_name = self.coordinator.device.nick_name
        device_registry.async_update_device(
            device.id,
            new_connections=new_connections,
            name_by_user=nick_name if nick_name else None,
        )


class MammotionCameraBaseEntity(Camera, ABC):
    """Devices that support cameras."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_is_streaming = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: MammotionBaseUpdateCoordinator, key: str) -> None:
        """Initialize the Lawn Mower."""
        super().__init__()
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.unique_name}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the HA device-registry info for this mower entity."""
        mower = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        swversion = mower.device_firmwares.device_version

        model_id = None
        if mower is not None:
            if mower.mower_state.model_id != "":
                model_id = mower.mower_state.model_id
            if (
                mower.mqtt_properties is not None
                and mower.mqtt_properties.params.items.extMod is not None
            ):
                model_id = mower.mqtt_properties.params.items.extMod.value

        connections: set[tuple[str, str]] = set()

        if mower.mower_state.ble_mac != "":
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, mower.mower_state.ble_mac.upper(), True
            )
            connections.add(
                (
                    CONNECTION_BLUETOOTH,
                    format_mac(
                        ble_device.address if ble_device else mower.mower_state.ble_mac
                    ),
                )
            )

        if mower.mower_state.wifi_mac != "":
            connections.add(
                (
                    CONNECTION_NETWORK_MAC,
                    format_mac(mower.mower_state.wifi_mac),
                )
            )

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.unique_name)},
            manufacturer="Mammotion",
            serial_number=self.coordinator.device_name.split("-", 1)[-1],
            model_id=model_id,
            name=self.coordinator.device_name,
            sw_version=swversion,
            model=self.coordinator.device.product_model or model_id,
            suggested_area="Garden",
            connections=connections,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.data is not None and self.coordinator.is_online()
