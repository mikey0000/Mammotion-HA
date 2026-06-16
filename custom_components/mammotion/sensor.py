"""Creates the sensor entities for the mower."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import time
from functools import partial

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from pymammotion.data.model.device import (
    MowingDevice,
    PoolCleanerDevice,
    RTKBaseStationDevice,
)
from pymammotion.data.model.enums import RTKStatus, TaskAreaStatus
from pymammotion.data.model.pool_state import SpinoSysStatus, SpinoWorkMode
from pymammotion.utility.constant import VioState
from pymammotion.utility.constant.device_constant import (
    AppConnectType,
    PosType,
    RTKPositionMode,
    camera_brightness,
    device_connection,
    device_mode,
)
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .const import DOMAIN
from .coordinator import (
    MAP_SYNC_STATUSES,
    MammotionBaseUpdateCoordinator,
    MammotionDeviceErrorUpdateCoordinator,
    MammotionReportUpdateCoordinator,
    MammotionRTKCoordinator,
    MammotionSpinoCoordinator,
)
from .entity import (
    MammotionBaseEntity,
    MammotionBaseRTKEntity,
    MammotionBaseSpinoEntity,
)


class MowerDataFormatter:
    """Helper class for formatting mower data."""

    @staticmethod
    def parse_time_string(time_str: str) -> time:
        """Convert a minutes-from-midnight string to a time object.

        Args:
            time_str: Integer minutes from midnight as a string (e.g., '1320' for 22:00).

        Returns:
            time object

        """
        if not time_str:
            return time(0, 0)
        try:
            total_minutes = int(time_str)
        except ValueError:
            return time(0, 0)
        return time(total_minutes // 60 % 24, total_minutes % 60)

    @staticmethod
    def format_time(time_str: str) -> str:
        """Convert time string to 12-hour format string.

        Args:
            time_str: Time in format 'HHMM' (e.g., '1330')

        Returns:
            Formatted string (e.g., '01:30pm')

        """
        t = MowerDataFormatter.parse_time_string(time_str)
        return t.strftime("%I:%M%p").lower()

    @staticmethod
    def format_time_range(start: str, end: str) -> str:
        """Format time range from decimal hours."""
        if start == "" or end == "":
            return "Not set"

        return f"{MowerDataFormatter.format_time(start)} - {MowerDataFormatter.format_time(end)}"


@dataclass(frozen=True, kw_only=True)
class MammotionSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion sensor entity."""

    value_fn: Callable[[MowingDevice], StateType]


@dataclass(frozen=True, kw_only=True)
class MammotionRTKSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion RTK sensor entity."""

    value_fn: Callable[[RTKBaseStationDevice], StateType]


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion Spino pool cleaner sensor entity."""

    value_fn: Callable[[PoolCleanerDevice], StateType]


@dataclass(frozen=True, kw_only=True)
class MammotionWorkSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion sensor entity."""

    value_fn: Callable[[MammotionReportUpdateCoordinator, MowingDevice], StateType]


@dataclass(frozen=True, kw_only=True)
class MammotionErrorSensorEntityDescription(SensorEntityDescription):
    """Describes Mammotion sensor entity."""

    value_fn: Callable[[MammotionDeviceErrorUpdateCoordinator, MowingDevice], StateType]


@dataclass(frozen=True, kw_only=True)
class MammotionSpinoErrorSensorEntityDescription(SensorEntityDescription):
    """Describes a Spino error-log sensor entity."""

    value_fn: Callable[[MammotionSpinoCoordinator], StateType]


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
        key="visual_positioning_status",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: VioState(
            mower_data.report_data.vision_info.vio_state
        ).name,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="maintenance_distance",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        value_fn=lambda mower_data: mower_data.report_data.maintenance.mileage,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
    ),
    MammotionSensorEntityDescription(
        key="maintenance_work_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda mower_data: mower_data.report_data.maintenance.work_time,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
    ),
    MammotionSensorEntityDescription(
        key="blade_used_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda mower_data: mower_data.report_data.maintenance.blade_used_time.blade_used_time,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
    ),
    MammotionSensorEntityDescription(
        key="blade_used_warn_time",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda mower_data: mower_data.report_data.maintenance.blade_used_time.blade_used_warn_time,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
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
        key="non_work_hours",
        value_fn=lambda mower_data: MowerDataFormatter.format_time_range(
            mower_data.non_work_hours.start_time,
            mower_data.non_work_hours.end_time,
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="pos_level",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: mower_data.report_data.rtk.pos_level,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="age",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda mower_data: mower_data.report_data.rtk.age,
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
        key="positioning_mode",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: str(
            RTKStatus.from_value(mower_data.report_data.rtk.status)
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="position_mode",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        value_fn=lambda mower_data: RTKPositionMode(
            mower_data.report_data.basestation_info.rtk_status
        ).name,
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
        value_fn=lambda coordinator, mower_data: (
            msg[:255] if (msg := coordinator.get_error_message(1)) is not None else None
        ),
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

# Luba 2 / Yuka (non-RTK) only — APK refreshNonRtkDeviceUI shows these;
# Luba 1 and standard RTK devices do not display them.
LUBA_2_YUKA_SIGNAL_TYPES: tuple[MammotionSensorEntityDescription, ...] = (
    MammotionSensorEntityDescription(
        key="l1_satellites",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.report_data.rtk.dis_status >> 16) & 255,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="l2_satellites",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.report_data.rtk.dis_status >> 24) & 255,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="co_view_l1",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: mower_data.report_data.rtk.co_view_stars & 255,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="co_view_l2",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.report_data.rtk.co_view_stars >> 8)
        & 255,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="rtk_signal",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.report_data.rtk.dis_status >> 40) & 255,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSensorEntityDescription(
        key="device_signal",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.report_data.rtk.dis_status >> 32) & 255,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

# Luba 1 only — APK refreshLuba1ModeUI shows base_link_status (connection_to_ref);
# Luba 2 / Yuka and RTK devices hide it.
LUBA_1_SIGNAL_TYPES: tuple[MammotionSensorEntityDescription, ...] = (
    MammotionSensorEntityDescription(
        key="base_link_status",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda mower_data: (mower_data.report_data.rtk.dis_status >> 48) & 255,
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
    MammotionWorkSensorEntityDescription(
        key="map_sync_status",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        options=list(MAP_SYNC_STATUSES),
        native_unit_of_measurement=None,
        value_fn=lambda coordinator, mower_data: coordinator.map_sync_status,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionWorkSensorEntityDescription(
        key="mqtt_status",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        options=["reported_online", "reported_offline"],
        native_unit_of_measurement=None,
        value_fn=lambda coordinator, mower_data: (
            "reported_online" if coordinator.mqtt_device_online else "reported_offline"
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
    MammotionRTKSensorEntityDescription(
        key="rtk_sats_num",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda rtk_data: rtk_data.sats_num,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionRTKSensorEntityDescription(
        key="position_mode",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        value_fn=lambda rtk_data: RTKPositionMode(rtk_data.rtk_status).name,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionRTKSensorEntityDescription(
        key="rtk_app_connect_type",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        value_fn=lambda rtk_data: AppConnectType(rtk_data.app_connect_type).name,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

SPINO_SENSOR_TYPES: tuple[MammotionSpinoSensorEntityDescription, ...] = (
    MammotionSpinoSensorEntityDescription(
        key="spino_battery",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda spino_data: spino_data.pool_state.battery,
    ),
    MammotionSpinoSensorEntityDescription(
        key="spino_status",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        options=[status.name for status in SpinoSysStatus],
        value_fn=lambda spino_data: spino_data.pool_state.sys_status.name,
    ),
    MammotionSpinoSensorEntityDescription(
        key="spino_work_mode",
        state_class=None,
        device_class=SensorDeviceClass.ENUM,
        options=[mode.name for mode in SpinoWorkMode],
        value_fn=lambda spino_data: spino_data.pool_state.work_mode.name,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

SPINO_ERROR_SENSOR_TYPES: tuple[MammotionSpinoErrorSensorEntityDescription, ...] = (
    MammotionSpinoErrorSensorEntityDescription(
        key="spino_error_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda coordinator: coordinator.get_error_time(),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSpinoErrorSensorEntityDescription(
        key="spino_error_message",
        state_class=None,
        native_unit_of_measurement=None,
        device_class=None,
        value_fn=lambda coordinator: (
            msg[:255] if (msg := coordinator.get_error_message()) is not None else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSpinoErrorSensorEntityDescription(
        key="spino_error_code",
        state_class=None,
        native_unit_of_measurement=None,
        device_class=None,
        value_fn=lambda coordinator: coordinator.get_error_code(),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MammotionSpinoErrorSensorEntityDescription(
        key="spino_mqtt_status",
        state_class=None,
        native_unit_of_measurement=None,
        device_class=SensorDeviceClass.ENUM,
        options=["online", "offline"],
        value_fn=lambda coordinator: "online"
        if coordinator.mqtt_device_online
        else "offline",
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
            entities.extend(
                MammotionSensorEntity(mower.reporting_coordinator, description)
                for description in LUBA_2_YUKA_SIGNAL_TYPES
            )
            device_type = DeviceType.value_of_str(
                mower.device.device_name, mower.device.product_key
            )
            if device_type.supports_battery_cycle_count():
                entities.extend(
                    MammotionSensorEntity(mower.reporting_coordinator, description)
                    for description in MINI_SERIES_EXCLUDED_TYPES
                )

        if DeviceType.is_luba1(mower.device.device_name, mower.device.product_key):
            entities.extend(
                MammotionSensorEntity(mower.reporting_coordinator, description)
                for description in LUBA_1_SIGNAL_TYPES
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

        # Dynamic task-area sensors — one per zone in the active mow task.
        # Added/removed as work_tasks_event.ids changes.
        added_task_areas: set[int] = set()
        task_area_entities: dict[int, MammotionTaskAreaSensorEntity] = {}
        update_task_areas = partial(
            async_add_task_area_entities,
            mower.reporting_coordinator,
            added_task_areas,
            task_area_entities,
            async_add_entities,
        )
        update_task_areas()
        entry.async_on_unload(
            mower.reporting_coordinator.async_add_listener(update_task_areas)
        )

    mammotion_rtks = entry.runtime_data.RTK
    for rtk in mammotion_rtks:
        entities.extend(
            MammotionRTKSensorEntity(rtk.coordinator, description)
            for description in RTK_SENSOR_TYPES
        )

    mammotion_spinos = entry.runtime_data.spino
    for spino in mammotion_spinos:
        entities.extend(
            MammotionSpinoSensorEntity(spino.coordinator, description)
            for description in SPINO_SENSOR_TYPES
        )
        entities.extend(
            MammotionSpinoErrorSensorEntity(spino.coordinator, description)
            for description in SPINO_ERROR_SENSOR_TYPES
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


class MammotionSpinoSensorEntity(MammotionBaseSpinoEntity, SensorEntity):
    """Defining the Mammotion Spino pool cleaner Sensor."""

    entity_description: MammotionSpinoSensorEntityDescription

    def __init__(
        self,
        coordinator: MammotionSpinoCoordinator,
        entity_description: MammotionSpinoSensorEntityDescription,
    ) -> None:
        """Set up MammotionSpinoSensor."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)


class MammotionSpinoErrorSensorEntity(MammotionBaseSpinoEntity, SensorEntity):
    """Sensor entity for a single Spino error-log field (code, time, or message)."""

    entity_description: MammotionSpinoErrorSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionSpinoCoordinator,
        entity_description: MammotionSpinoErrorSensorEntityDescription,
    ) -> None:
        """Set up MammotionSpinoErrorSensorEntity."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        self._attr_translation_key = entity_description.key

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator)


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


class MammotionTaskAreaSensorEntity(MammotionBaseEntity, SensorEntity):
    """Dynamic per-zone task-status sensor, driven by a MammotionSensorEntityDescription.

    One entity is created per zone hash present in work_tasks_event.ids.
    native_value is driven by entity_description.value_fn so that enum changes
    are reflected automatically on every coordinator update, exactly like all
    other description-based sensors.

    translation_key / translation_placeholders / device_class / options are all
    read by HA from entity_description, so we never hard-code them on the class.
    """

    entity_description: MammotionSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MammotionReportUpdateCoordinator,
        entity_description: MammotionSensorEntityDescription,
    ) -> None:
        """Initialise from a description that captures the zone hash via closure."""
        super().__init__(coordinator, entity_description.key)
        self.entity_description = entity_description
        # Do NOT set _attr_translation_key here — HA reads it from
        # entity_description.translation_key ("task_area_status").
        # Do NOT set _attr_translation_placeholders — HA reads it from
        # entity_description.translation_placeholders ({"name": area_name}).

    @property
    def native_value(self) -> StateType:
        """Return the state via value_fn, identical to MammotionSensorEntity."""
        return self.entity_description.value_fn(self.coordinator.data)

    def update_name(self, new_name: str) -> None:
        """Refresh the display name when the area is renamed on the device.

        Overrides _attr_translation_placeholders so HA picks up the new name
        on the next state write without recreating the entity.
        """
        self._attr_translation_placeholders = {"name": new_name}
        if self.hass is not None:
            self.async_write_ha_state()


_TASK_AREA_OPTIONS: list[str] = [s.name for s in TaskAreaStatus]


@callback
def async_add_task_area_entities(
    coordinator: MammotionReportUpdateCoordinator,
    added_task_areas: set[int],
    entities_by_hash: dict[int, MammotionTaskAreaSensorEntity],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sync task-area sensor entities with the current work_tasks_event.ids.

    Called every time the coordinator updates.  New zone hashes get a new
    sensor entity; hashes that have left the task have their entity removed
    from the registry.
    """
    if coordinator.data is None:
        return

    current_ids: set[int] = set(coordinator.data.events.work_tasks_event.ids)

    new_hashes = current_ids - added_task_areas
    sensor_entities: list[MammotionTaskAreaSensorEntity] = []

    for area_hash in sorted(new_hashes):
        area_name = coordinator.get_area_entity_name(area_hash) or f"area {area_hash}"
        if area_hash in entities_by_hash:
            # Zone reappeared (e.g. task restarted) — refresh display name only.
            entities_by_hash[area_hash].update_name(area_name)
            added_task_areas.add(area_hash)
            continue
        description = MammotionSensorEntityDescription(
            key=f"{area_hash}_task_area",
            translation_key="task_area_status",
            translation_placeholders={"name": area_name},
            device_class=SensorDeviceClass.ENUM,
            state_class=None,
            options=_TASK_AREA_OPTIONS,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda mower_data, h=area_hash: getattr(
                mower_data.events.work_tasks_event.hash_area_map.get(h), "name", None
            ),
        )
        entity = MammotionTaskAreaSensorEntity(coordinator, description)
        sensor_entities.append(entity)
        entities_by_hash[area_hash] = entity
        added_task_areas.add(area_hash)

    old_hashes = added_task_areas - current_ids
    if old_hashes:
        _async_remove_task_area_entities(coordinator, old_hashes)
        for h in old_hashes:
            added_task_areas.discard(h)
            entities_by_hash.pop(h, None)

    if sensor_entities:
        async_add_entities(sensor_entities)


def _async_remove_task_area_entities(
    coordinator: MammotionBaseUpdateCoordinator,
    old_hashes: set[int],
) -> None:
    """Remove task-area sensor entities from the HA entity registry."""
    registry = er.async_get(coordinator.hass)
    for area_hash in old_hashes:
        entity_id = registry.async_get_entity_id(
            SENSOR_DOMAIN, DOMAIN, f"{coordinator.unique_name}_{area_hash}_task_area"
        )
        if entity_id:
            registry.async_remove(entity_id)
