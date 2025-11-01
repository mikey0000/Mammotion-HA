"""Creates the sensor entities for the mower."""

import math
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfArea,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util.unit_conversion import SpeedConverter
from pymammotion.data.model.device import MowingDevice, RTKDevice
from pymammotion.data.model.enums import RTKStatus
from pymammotion.utility.constant.device_constant import (
    PosType,
    camera_brightness,
    device_connection,
    device_mode,
)
from pymammotion.utility.device_type import DeviceType

from . import (
    MammotionConfigEntry,
    MammotionDeviceErrorUpdateCoordinator,
    MammotionReportUpdateCoordinator,
    MammotionRTKCoordinator,
)
from .entity import MammotionBaseEntity, MammotionBaseRTKEntity

SPEED_UNITS = SpeedConverter.VALID_UNITS


@dataclass(frozen=True, kw_only=True)
class MammotionSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion sensor entity."""

    value_fn: Callable[[MowingDevice], StateType]


@dataclass(frozen=True, kw_only=True)
class MammotionRTKSensorEntityDescription(SensorEntityDescription):
    value_fn: Callable[[RTKDevice], StateType]


@dataclass(frozen=True, kw_only=True)
class MammotionWorkSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion sensor entity."""

    value_fn: Callable[[MammotionReportUpdateCoordinator, MowingDevice], StateType]


@dataclass(frozen=True, kw_only=True)
class MammotionErrorSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion sensor entity."""

    value_fn: Callable[[MammotionDeviceErrorUpdateCoordinator, MowingDevice], StateType]


LUBA_SENSOR_ONLY_TYPES: tuple[MammotionSensorEntityDescription, ...] = (
    MammotionSensorEntityDescription(
        key="blade_height",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        value_fn=lambda mower_data: mower_data.report_data.work.knife_height,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

LUBA_2_YUKA_ONLY_TYPES: tuple[MammotionSensorEntityDescription, ...] = (
    MammotionSensorEntityDescription(
        key="camera_brightness",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        value_fn=lambda mower_data: camera_brightness(
            mower_data.report_data.vision_info.brightness
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="maintenance_distance",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        value_fn=lambda mower_data: mower_data.report_data.maintenance.mileage,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="maintenance_work_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda mower_data: mower_data.report_data.maintenance.work_time,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

MINI_SERIES_EXCLUDED_TYPES: tuple[MammotionSensorEntityDescription, ...] = (
    MammotionSensorEntityDescription(
        key="maintenance_bat_cycles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda mower_data: mower_data.report_data.maintenance.bat_cycles,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

SENSOR_TYPES: tuple[MammotionSensorEntityDescription, ...] = (
    MammotionSensorEntityDescription(
        key="battery_percent",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda mower_data: mower_data.report_data.dev.battery_val,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="ble_rssi",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        value_fn=lambda mower_data: mower_data.report_data.connect.ble_rssi,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="wifi_rssi",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        value_fn=lambda mower_data: mower_data.report_data.connect.wifi_rssi,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="mnet_rssi",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        value_fn=lambda mower_data: mower_data.report_data.connect.mnet_rssi,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="connect_type",
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: device_connection(mower_data.report_data.connect),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="gps_stars",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: mower_data.report_data.rtk.gps_stars,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="area",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        value_fn=lambda mower_data: mower_data.report_data.work.area & 65535,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="mowing_speed",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        value_fn=lambda mower_data: mower_data.report_data.work.man_run_speed / 100,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="progress",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda mower_data: mower_data.report_data.work.area >> 16,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="total_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda mower_data: mower_data.report_data.work.progress & 65535,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="elapsed_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda mower_data: (mower_data.report_data.work.progress & 65535)
        - (mower_data.report_data.work.progress >> 16),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="left_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda mower_data: mower_data.report_data.work.progress >> 16,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="l1_satellites",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.report_data.rtk.co_view_stars >> 0)
        & 255,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="l2_satellites",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.report_data.rtk.co_view_stars >> 8)
        & 255,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # MammotionSensorEntityDescription(
    #     key="vlsam_status",
    #     state_class=SensorStateClass.MEASUREMENT,
    #     device_class=None,
    #     native_unit_of_measurement=None,
    #     value_fn=lambda mower_data: (mower_data.report_data.dev.vslam_status & 65280) >> 8,
    # ),
    MammotionSensorEntityDescription(
        key="activity_mode",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        value_fn=lambda mower_data: device_mode(mower_data.report_data.dev.sys_status),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="position_mode",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: str(
            RTKStatus.from_value(mower_data.report_data.rtk.status)
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="position_type",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: str(
            PosType(mower_data.location.position_type).name
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="rtk_latitude",
        native_unit_of_measurement=DEGREE,
        value_fn=lambda mower_data: mower_data.location.RTK.latitude * 180.0 / math.pi,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="rtk_longitude",
        native_unit_of_measurement=DEGREE,
        value_fn=lambda mower_data: mower_data.location.RTK.longitude * 180.0 / math.pi,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # MammotionSensorEntityDescription(
    #     key="lawn_mower_position",
    #     state_class=None,
    #     device_class=None,  # Set device class to "geo_location"
    #     native_unit_of_measurement=None,
    #     value_fn=lambda mower_data: f"{mower_data.location.device.latitude}, {mower_data.location.device.longitude}"
    # )
    # ToDo: We still need to add the following.
    # - RTK Status - None, Single, Fix, Float, Unknown (RTKStatusFragment.java)
    # - Signal quality (Robot)
    # - Signal quality (Ref. Station)
    # - LoRa number
    # - WiFi status
    # 'real_pos_x': -142511, 'real_pos_y': -20548, 'real_toward': 50915, (robot position)
)

SENSOR_ERROR_TYPES: tuple[MammotionErrorSensorEntityDescription, ...] = (
    MammotionErrorSensorEntityDescription(
        key="error_1_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda coordinator, mower_data: coordinator.get_error_time(1),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionErrorSensorEntityDescription(
        key="error_1_message",
        state_class=None,
        native_unit_of_measurement=None,
        device_class=None,
        value_fn=lambda coordinator, mower_data: coordinator.get_error_message(1),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionErrorSensorEntityDescription(
        key="error_1_code",
        state_class=None,
        native_unit_of_measurement=None,
        device_class=None,
        value_fn=lambda coordinator, mower_data: coordinator.get_error_code(1),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

WORK_SENSOR_TYPES: tuple[MammotionWorkSensorEntityDescription, ...] = (
    MammotionWorkSensorEntityDescription(
        key="work_area",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        value_fn=lambda coordinator, mower_data: str(
            coordinator.get_area_entity_name(mower_data.location.work_zone)
            or "Not working"
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

RTK_SENSOR_TYPES: tuple[MammotionRTKSensorEntityDescription, ...] = (
    MammotionRTKSensorEntityDescription(
        key="rtk_lora",
        value_fn=lambda rtk_data: rtk_data.lora_version,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionRTKSensorEntityDescription(
        key="rtk_latitude",
        native_unit_of_measurement=DEGREE,
        value_fn=lambda rtk_data: rtk_data.lat * 180 / math.pi,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionRTKSensorEntityDescription(
        key="rtk_longitude",
        native_unit_of_measurement=DEGREE,
        value_fn=lambda rtk_data: rtk_data.lon * 180 / math.pi,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionRTKSensorEntityDescription(
        key="rtk_wifi_rssi",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        value_fn=lambda rtk_data: rtk_data.wifi_rssi,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    mammotion_mowers = entry.runtime_data.mowers

    entities = []
    for mower in mammotion_mowers:
        if not DeviceType.is_yuka(mower.device.device_name):
            entities.extend(
                MammotionSensorEntity(mower.reporting_coordinator, description)
                for description in LUBA_SENSOR_ONLY_TYPES
            )

        if DeviceType.is_luba_pro(mower.device.device_name):
            entities.extend(
                MammotionSensorEntity(mower.reporting_coordinator, description)
                for description in LUBA_2_YUKA_ONLY_TYPES
            )
            if not DeviceType.is_yuka_mini(mower.device.device_name):
                entities.extend(
                    MammotionSensorEntity(mower.reporting_coordinator, description)
                    for description in MINI_SERIES_EXCLUDED_TYPES
                )

        entities.extend(
            MammotionSensorEntity(mower.reporting_coordinator, description)
            for description in SENSOR_TYPES
        )
        entities.extend(
            MammotionWorkSensorEntity(mower.reporting_coordinator, description)
            for description in WORK_SENSOR_TYPES
        )

        entities.extend(
            MammotionErrorSensorEntity(mower.error_coordinator, description)
            for description in SENSOR_ERROR_TYPES
        )

    mammotion_rtks = entry.runtime_data.RTK
    for rtk in mammotion_rtks:
        entities.extend(
            MammotionRTKSensorEntity(rtk.coordinator, description)
            for description in RTK_SENSOR_TYPES
        )

    async_add_entities(entities)


class MammotionSensorEntity(MammotionBaseEntity, SensorEntity):
    """Defining the Mammotion Sensor."""

    entity_description: MammotionSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionReportUpdateCoordinator,
        entity_description: MammotionSensorEntityDescription,
    ) -> None:
        """Set up MammotionSensor."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)


class MammotionRTKSensorEntity(MammotionBaseRTKEntity, SensorEntity):
    """Defining the Mammotion Sensor."""

    entity_description: MammotionRTKSensorEntityDescription

    def __init__(
        self,
        coordinator: MammotionRTKCoordinator,
        entity_description: MammotionRTKSensorEntityDescription,
    ) -> None:
        """Set up MammotionSensor."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)


class MammotionErrorSensorEntity(MammotionBaseEntity, SensorEntity):
    """Defining the Mammotion Error Sensor."""

    entity_description: MammotionErrorSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionDeviceErrorUpdateCoordinator,
        entity_description: MammotionErrorSensorEntityDescription,
    ) -> None:
        """Set up MammotionSensor."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator, self.coordinator.data)


class MammotionWorkSensorEntity(MammotionBaseEntity, SensorEntity):
    """Defining the Mammotion Sensor."""

    entity_description: MammotionWorkSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionReportUpdateCoordinator,
        entity_description: MammotionWorkSensorEntityDescription,
    ) -> None:
        """Set up MammotionSensor."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator, self.coordinator.data)
