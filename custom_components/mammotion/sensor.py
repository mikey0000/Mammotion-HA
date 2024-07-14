"""Creates the sensor entities for the mower."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from pymammotion.data.model.enums import RTKStatus
from pymammotion.proto.luba_msg import ReportInfoData

from . import MammotionConfigEntry
from .coordinator import MammotionDataUpdateCoordinator
from .entity import MammotionBaseEntity


@dataclass(frozen=True, kw_only=True)
class MammotionSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion sensor entity."""

    value_fn: Callable[[ReportInfoData], StateType]


SENSOR_TYPES: tuple[MammotionSensorEntityDescription, ...] = (
    MammotionSensorEntityDescription(
        key="battery_percent",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda mower_data: mower_data.dev.battery_val,
    ),
    MammotionSensorEntityDescription(
        key="ble_rssi",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        value_fn=lambda mower_data: mower_data.connect.ble_rssi,
    ),
    MammotionSensorEntityDescription(
        key="wifi_rssi",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        value_fn=lambda mower_data: mower_data.connect.wifi_rssi,
    ),
    MammotionSensorEntityDescription(
        key="gps_stars",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: mower_data.rtk.gps_stars,
    ),
    MammotionSensorEntityDescription(
        key="blade_height",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement="mm",
        value_fn=lambda mower_data: mower_data.work.knife_height,
    ),
    MammotionSensorEntityDescription(
        key="area",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement="m²",
        value_fn=lambda mower_data: mower_data.work.area & 65535,
    ),
    MammotionSensorEntityDescription(
        key="mowing_speed",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement="m/s",
        value_fn=lambda mower_data: mower_data.work.man_run_speed / 100,
    ),
    MammotionSensorEntityDescription(
        key="progress",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda mower_data: mower_data.work.area >> 16,
    ),
    MammotionSensorEntityDescription(
        key="total_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        value_fn=lambda mower_data: mower_data.work.progress & 65535,
    ),
    MammotionSensorEntityDescription(
        key="elapsed_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        value_fn=lambda mower_data: (mower_data.work.progress & 65535)
        - (mower_data.work.progress >> 16),
    ),
    MammotionSensorEntityDescription(
        key="left_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        value_fn=lambda mower_data: mower_data.work.progress >> 16,
    ),
    MammotionSensorEntityDescription(
        key="l1_satellites",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.rtk.co_view_stars >> 0) & 255,
    ),
    MammotionSensorEntityDescription(
        key="l2_satellites",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.rtk.co_view_stars >> 8) & 255,
    ),
    MammotionSensorEntityDescription(
        key="position_mode",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: str(
            RTKStatus.from_value(mower_data.rtk.status)
        ),  # Note: This will not work for Luba2 & Yuka. Only for Luba1
    ),
    # ToDo: We still need to add the following.
    # - RTK Status - None, Single, Fix, Float, Unknown (RTKStatusFragment.java)
    # - Signal quality (Robot)
    # - Signal quality (Ref. Station)
    # - LoRa number
    # - Multi-point turn
    # - Transverse mode
    # - WiFi status
    # - Side LED
    # - Possibly more I forgot about
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    coordinator = entry.runtime_data
    async_add_entities(
        MammotionSensorEntity(coordinator, description) for description in SENSOR_TYPES
    )


class MammotionSensorEntity(MammotionBaseEntity, SensorEntity):
    """Defining the Mammotion Sensor."""

    entity_description: MammotionSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionDataUpdateCoordinator,
        description: MammotionSensorEntityDescription,
    ) -> None:
        """Set up MammotionSensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(
            self.coordinator.data.sys.toapp_report_data
        )
