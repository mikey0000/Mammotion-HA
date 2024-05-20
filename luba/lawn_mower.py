"""Luba lawn mowers."""
from __future__ import annotations

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyluba.utility.constant.device_constant import device_mode
from .coordinator import MammotionDataUpdateCoordinator
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN

SUPPORTED_FEATURES = LawnMowerEntityFeature.DOCK | LawnMowerEntityFeature.PAUSE | LawnMowerEntityFeature.START_MOWING

async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    coordinator: MammotionDataUpdateCoordinator,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up luba lawn mower."""
    
    print(discovery_info)
    
    async_add_entities(
        [
            MammotionLawnMowerEntity(config.get('title'), coordinator),
        ]
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Luba config entry."""
    coordinator: MammotionDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    await async_setup_platform(hass, {'title': entry.title}, coordinator, async_add_entities)


class MammotionLawnMowerEntity( CoordinatorEntity[MammotionDataUpdateCoordinator], LawnMowerEntity):
    """Representation of a Luba lawn mower."""
    
    _attr_supported_features = SUPPORTED_FEATURES
    _attr_has_entity_name = True

    def __init__(
        self,
        device_name: str,
        coordinator: MammotionDataUpdateCoordinator,
        features: LawnMowerEntityFeature = LawnMowerEntityFeature(0),
    ) -> None:
        """Initialize the lawn mower."""
        super().__init__(coordinator)
        self._attr_name = device_name
        self._attr_unique_id = f"{device_name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_name)},
            manufacturer="Mammotion",
            name=device_name,
            suggested_area="Garden",
        )
        self._attr_supported_features = features
        
        activity = LawnMowerActivity.DOCKED
        
        self._attr_activity = activity
        
    @property
    def activity(self) -> LawnMowerActivity:
        """Return the state of the mower."""
        # productkey = coordinator.device.raw_data['net']['toappWifiIotStatus']['productkey']
        # devicename = coordinator.device.raw_data['net']['toappWifiIotStatus']['devicename']
        mode = "MODE_READY"
        if self.coordinator.device.raw_data.get('dev'):
            mode = device_mode(self.coordinator.device.raw_data['dev']['sysStatus'])
        
        if mode == "MODE_PAUSE":
            return LawnMowerActivity.PAUSED
        if mode == "MODE_WORKING":
            return LawnMowerActivity.MOWING
        if mode == "MODE_LOCK":
            return LawnMowerActivity.ERROR
        return LawnMowerActivity.DOCKED


    async def async_start_mowing(self) -> None:
        """Start mowing."""
        self._attr_activity = LawnMowerActivity.MOWING
        self.async_write_ha_state()

    async def async_dock(self) -> None:
        """Start docking."""
        self._attr_activity = LawnMowerActivity.DOCKED
        self.async_write_ha_state()

    async def async_pause(self) -> None:
        """Pause mower."""
        self._attr_activity = LawnMowerActivity.PAUSED
        self.async_write_ha_state()
        
        
    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        print("coordinator callback")
        print(self.coordinator.device.raw_data)
        self.activity()

        self.async_write_ha_state()
