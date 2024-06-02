"""Creates the sensor entities for the mower."""

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN
from .coordinator import MammotionDataUpdateCoordinator
from .entity import MammotionBaseEntity


@dataclass(frozen=True, kw_only=True)
class MammotionSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion sensor entity."""

    value_fn: Callable[[], StateType]


SENSOR_TYPES: tuple[MammotionSensorEntityDescription, ...] = (
    MammotionSensorEntityDescription(
        key="battery_percent",
        name="Battery",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda coordinator: coordinator.device.luba_msg.sys.toapp_report_data.dev.battery_val,
    ),
    MammotionSensorEntityDescription(
        key="ble_rssi",
        name="BLE RSSI",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        value_fn=lambda coordinator: coordinator.device.luba_msg.sys.toapp_report_data.connect.ble_rssi,
    ),
    MammotionSensorEntityDescription(
        key="wifi_rssi",
        name="WiFi RSSI",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        value_fn=lambda coordinator: coordinator.device.luba_msg.sys.toapp_report_data.connect.wifi_rssi,
    ),
    MammotionSensorEntityDescription(
        key="gps_stars",
        name="Satellites (Robot)",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda coordinator: coordinator.device.luba_msg.sys.toapp_report_data.rtk.gps_stars,
    ),
     MammotionSensorEntityDescription(
        key="blade_height",
        name="Blade Height",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement="mm",
        value_fn=lambda coordinator: coordinator.device.luba_msg.sys.toapp_report_data.work.knife_height,
    ),
    MammotionSensorEntityDescription(
        key="area",
        name="Area",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement="m²",
        value_fn=lambda coordinator: coordinator.device.luba_msg.sys.toapp_report_data.work.area,
    ),
    MammotionSensorEntityDescription(
        key="remaining_mow_time",
        name="Remaining Mow Time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        value_fn=lambda coordinator: coordinator.device.luba_msg.sys.toapp_report_data.work.man_run_speed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensor platform."""
    coordinator: MammotionDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_name = entry.title
    async_add_entities(
        MammotionSensorEntity(device_name, coordinator, description)
        for description in SENSOR_TYPES
    )

class MammotionSensorEntity(MammotionBaseEntity, SensorEntity):
    """Defining the Mammotion Sensor."""

    entity_description: MammotionSensorEntityDescription

    def __init__(
        self,
        device_name: str,
        coordinator: MammotionDataUpdateCoordinator,
        description: MammotionSensorEntityDescription,
    ) -> None:
        """Set up MammotionSensor."""
        super().__init__(device_name, coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{device_name}_{description.key}"
        self._attr_name = description.name
        # self.entity_id = f"{DOMAIN}.{device_name}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        print("================= Debug Log =================")
        print(self.coordinator.device.luba_msg)
        # print(self.coordinator.device.raw_data)
        print("==================================")
        return self.entity_description.value_fn(self.coordinator)
