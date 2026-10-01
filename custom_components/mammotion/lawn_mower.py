"""Mammotion Lawn Mower."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import copy
from datetime import time
from typing import Any, cast

import voluptuous as vol
from homeassistant.components.lawn_mower import DOMAIN as LAWN_MOWER_DOMAIN
from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import service
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.data.model.report_info import DeviceData, ReportData
from pymammotion.messaging.command_queue import Priority
from pymammotion.utility.constant.device_constant import WorkMode
from pymammotion.utility.device_type import DeviceType

from . import MammotionConfigEntry
from .const import COMMAND_EXCEPTIONS, DOMAIN, LOGGER
from .coordinator import MammotionReportUpdateCoordinator
from .entity import MammotionBaseEntity, supports_grass_collection

SERVICE_START_MOWING = "start_mow"
SERVICE_MODIFY_RUNNING_JOB = "modify_running_job"
SERVICE_CANCEL_JOB = "cancel_job"
SERVICE_START_STOP_BLADES = "start_stop_blades"
SERVICE_SET_NON_WORK_HOURS = "set_non_work_hours"
SERVICE_RESET_BLADE_TIME = "reset_blade_time"
SERVICE_SET_BLADE_WARNING_TIME = "set_blade_warning_time"
SERVICE_SET_BLADE_HEIGHT = "set_blade_height"

# Grass-collection point editing.  Services rather than buttons: a point is
# recorded at the mower's current position, so they are only useful driven in
# sequence against a mower that already has a map.
SERVICE_START_DUMP_POINT_SETUP = "start_dump_point_setup"
SERVICE_ADD_DUMP_POINT = "add_dump_point"
SERVICE_UNDO_DUMP_POINT = "undo_dump_point"
SERVICE_FINISH_DUMP_POINT_SETUP = "finish_dump_point_setup"
SERVICE_FINISH_OUTSIDE_DUMP_POINT = "finish_outside_dump_point"

START_MOW_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Optional("modify", default=False): cv.boolean,
    vol.Optional("plan_only", default=False): cv.boolean,
    # No defaults: a route field left out keeps the config entity's value.
    vol.Optional("is_mow"): cv.boolean,
    vol.Optional("is_dump"): cv.boolean,
    vol.Optional("is_edge"): cv.boolean,
    vol.Optional("collect_grass_frequency"): vol.All(
        vol.Coerce(int), vol.Range(min=5, max=100)
    ),
    vol.Optional("border_mode"): vol.All(vol.Coerce(int), vol.In([0, 1])),
    vol.Optional("job_version"): vol.Coerce(int),
    vol.Optional("job_id"): vol.Coerce(int),
    vol.Optional("speed"): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=1.2)),
    vol.Optional("ultra_wave"): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 10, 11])),
    vol.Optional("channel_mode"): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 3])),
    vol.Optional("channel_width"): vol.All(vol.Coerce(int), vol.Range(min=5, max=35)),
    vol.Optional("blade_height"): vol.All(vol.Coerce(int), vol.Range(min=15, max=100)),
    vol.Optional("toward"): vol.All(vol.Coerce(int), vol.Range(min=-180, max=180)),
    vol.Optional("toward_included_angle"): vol.All(
        vol.Coerce(int), vol.Range(min=-180, max=180)
    ),
    vol.Optional("toward_mode"): vol.All(vol.Coerce(int), vol.In([0, 1, 2])),
    vol.Optional("mowing_laps"): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 3, 4])),
    vol.Optional("obstacle_laps"): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 3, 4])),
    vol.Optional("start_progress"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    vol.Optional("auto_change_direction"): vol.All(vol.Coerce(int), vol.In([0, 1])),
    vol.Optional("ride_boundary_distance"): vol.All(
        vol.Coerce(float), vol.Range(min=0, max=1)
    ),
    vol.Optional("areas"): vol.All(cv.ensure_list, [cv.entity_id]),
}

#: How long to wait for the mower to report the mode a job transition leads to.
_MODE_WAIT_TIMEOUT = 60

#: Everything the app's in-job editor can change on a job already running.
#: Deliberately excludes the job's identity and geometry — areas, route angle,
#: route-angle mode and perimeter laps — which ``async_modify_plan_route``
#: forces back from the device so a tweak cannot re-plan the job.
MODIFY_RUNNING_JOB_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Optional("blade_height"): vol.All(vol.Coerce(int), vol.Range(min=15, max=100)),
    vol.Optional("speed"): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=1.2)),
    vol.Optional("ultra_wave"): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 10, 11, 12])),
    vol.Optional("channel_width"): vol.All(vol.Coerce(int), vol.Range(min=5, max=35)),
    vol.Optional("channel_mode"): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 3])),
    vol.Optional("obstacle_laps"): vol.All(vol.Coerce(int), vol.In([0, 1, 2, 3, 4])),
    vol.Optional("auto_change_direction"): vol.All(vol.Coerce(int), vol.In([0, 1])),
    # The cloud schema caps progress at 99.
    vol.Optional("start_progress"): vol.All(vol.Coerce(int), vol.Range(min=0, max=99)),
}

START_STOP_BLADES_SCHEMA = {
    vol.Required("start_stop", default=True): cv.boolean,
    vol.Optional("blade_height", default=30): vol.All(
        vol.Coerce(int), vol.Range(min=15, max=100)
    ),
}

SET_NON_WORK_HOURS_SCHEMA = {
    vol.Required("start_time"): cv.time,
    vol.Required("end_time"): cv.time,
}

SET_BLADE_WARNING_TIME_SCHEMA = {
    vol.Required("hours"): vol.All(vol.Coerce(int), vol.Range(min=1, max=9999)),
}

SET_BLADE_HEIGHT_SCHEMA = {
    vol.Required("height"): vol.All(vol.Coerce(int), vol.Range(min=15, max=100)),
}


def get_entity_attribute(
    hass: HomeAssistant, entity_id: str, attribute_name: str
) -> str | None:
    """Return a named attribute from a HA entity state, or None if unavailable."""
    # Get the state object of the entity
    entity = hass.states.get(entity_id)

    # Check if the entity exists and has attributes
    if entity and attribute_name in entity.attributes:
        # Return the specific attribute
        return cast(str | None, entity.attributes.get(attribute_name))
    # Return None if the entity or attribute does not exist
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Mammotion Lawn Mower config entry."""
    mammotion_devices = entry.runtime_data.mowers

    entities = [
        MammotionLawnMowerEntity(mower.reporting_coordinator)
        for mower in mammotion_devices
    ]

    async_add_entities(entities)

    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_MOWING,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=START_MOW_SCHEMA,
        func="async_start_mowing",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_MODIFY_RUNNING_JOB,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=MODIFY_RUNNING_JOB_SCHEMA,
        func="async_modify_running_job",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_CANCEL_JOB,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=None,
        func="async_cancel",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_STOP_BLADES,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=START_STOP_BLADES_SCHEMA,
        func="async_start_stop_blades",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_NON_WORK_HOURS,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=SET_NON_WORK_HOURS_SCHEMA,
        func="async_set_non_work_hours",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_RESET_BLADE_TIME,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=None,
        func="async_reset_blade_time",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_BLADE_WARNING_TIME,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=SET_BLADE_WARNING_TIME_SCHEMA,
        func="async_set_blade_warning_time",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_BLADE_HEIGHT,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=SET_BLADE_HEIGHT_SCHEMA,
        func="async_set_blade_height",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_DUMP_POINT_SETUP,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=None,
        func="async_start_dump_point_setup",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_ADD_DUMP_POINT,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=None,
        func="async_add_dump_point",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_UNDO_DUMP_POINT,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=None,
        func="async_undo_dump_point",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_FINISH_DUMP_POINT_SETUP,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=None,
        func="async_finish_dump_point_setup",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_FINISH_OUTSIDE_DUMP_POINT,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=None,
        func="async_finish_outside_dump_point",
    )


class MammotionLawnMowerEntity(MammotionBaseEntity, LawnMowerEntity):  # type: ignore[misc]
    """Representation of a Mammotion Lawn Mower."""

    _attr_supported_features = (
        LawnMowerEntityFeature.DOCK
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.START_MOWING
    )

    def __init__(self, coordinator: MammotionReportUpdateCoordinator) -> None:
        """Initialize the Lawn Mower."""
        super().__init__(coordinator, "mower")
        self._attr_name = None  # main feature of device

    @property
    def rpt_dev_status(self) -> DeviceData:
        """Return the device status."""
        return self.coordinator.data.report_data.dev

    @property
    def report_data(self) -> ReportData:
        """Return the report data."""
        return self.coordinator.data.report_data

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return the state of the mower."""

        charge_state = self.rpt_dev_status.charge_state
        mode = self.rpt_dev_status.sys_status
        if mode is None:
            return None

        LOGGER.debug("activity mode %s", mode)
        if mode in (WorkMode.MODE_PAUSE, WorkMode.MODE_CHARGING_PAUSE) or (
            mode == WorkMode.MODE_READY and charge_state == 0
        ):
            return LawnMowerActivity.PAUSED
        if mode == WorkMode.MODE_WORKING:
            return LawnMowerActivity.MOWING
        if mode == WorkMode.MODE_RETURNING:
            return LawnMowerActivity.RETURNING
        if mode == WorkMode.MODE_LOCK:
            return LawnMowerActivity.ERROR
        if mode == WorkMode.MODE_READY and charge_state != 0:
            return LawnMowerActivity.DOCKED
        return None

    async def async_start_mowing(self, **kwargs: Any) -> None:  # noqa: C901
        """Start a job, or resume the paused one when no new route is asked for."""
        trans_key = "pause_failed"

        await self.coordinator.async_ensure_fresh_state()

        modify_plan = kwargs.pop("modify", False)
        plan_only = kwargs.pop("plan_only", False)
        areas = [
            # TODO this should not need to be cast.
            int(entity_hash)
            for entity_id in kwargs.pop("areas", [])
            if (entity_hash := get_entity_attribute(self.hass, entity_id, "hash"))
            is not None
        ]
        new_job = bool(kwargs or areas or plan_only)

        # A copy: the route builder writes into the settings it is given.
        operational_settings = copy(self.coordinator.operation_settings)
        if areas:
            operational_settings.areas = list(dict.fromkeys(areas))
        for key, value in kwargs.items():
            setattr(operational_settings, key, value)
        LOGGER.debug(kwargs)
        LOGGER.debug(operational_settings)

        mode = self.rpt_dev_status.sys_status
        breakpoint_info = self.report_data.work.bp_info
        if mode is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_not_ready"
            )

        if mode in (
            WorkMode.MODE_PAUSE,
            WorkMode.MODE_READY,
            WorkMode.MODE_RETURNING,
            WorkMode.MODE_WORKING,
            WorkMode.MODE_INITIALIZATION,
        ):
            try:
                if modify_plan:
                    await self.coordinator.async_modify_plan_route(
                        operational_settings, priority=Priority.USER
                    )
                    return

                if new_job and mode in (
                    WorkMode.MODE_PAUSE,
                    WorkMode.MODE_WORKING,
                    WorkMode.MODE_RETURNING,
                ):
                    await self._async_end_job(mode)
                    trans_key = "start_failed"
                    mode = await self._async_wait_for_mode(WorkMode.MODE_READY)
                    # The breakpoint still reported belongs to the job just ended.
                    breakpoint_info = 0
                elif (
                    new_job
                    and mode in (WorkMode.MODE_READY, WorkMode.MODE_INITIALIZATION)
                    and breakpoint_info != 0
                ):
                    # The app refuses to plan a job while a breakpoint stands.
                    await self._async_end_job(WorkMode.MODE_PAUSE)
                    trans_key = "start_failed"
                    mode = await self._async_wait_until(
                        lambda: (
                            WorkMode(status)
                            if self.report_data.work.bp_info == 0
                            and (status := self.rpt_dev_status.sys_status)
                            in (WorkMode.MODE_READY, WorkMode.MODE_INITIALIZATION)
                            else None
                        )
                    )
                    breakpoint_info = 0
                elif mode == WorkMode.MODE_RETURNING:
                    trans_key = "dock_cancel_failed"
                    await self.coordinator.async_send_and_wait(
                        "cancel_return_to_dock",
                        "todev_taskctrl_ack",
                        priority=Priority.USER,
                    )
                    mode = await self._async_wait_for_mode(
                        WorkMode.MODE_PAUSE, WorkMode.MODE_READY
                    )
                if mode == WorkMode.MODE_PAUSE:
                    trans_key = "resume_failed"
                    if breakpoint_info != 0:
                        await self.coordinator.async_send_command(
                            "resume_execute_task", priority=Priority.USER
                        )
                        await self._async_planning_step(
                            self.coordinator.async_send_and_wait(
                                "query_generate_route_information",
                                "bidire_reqconver_path",
                                priority=Priority.USER,
                            )
                        )
                if mode in (WorkMode.MODE_READY, WorkMode.MODE_INITIALIZATION):
                    trans_key = "start_failed"
                    if breakpoint_info != 0:
                        await self._async_planning_step(
                            self.coordinator.async_send_and_wait(
                                "query_generate_route_information",
                                "bidire_reqconver_path",
                                priority=Priority.USER,
                            )
                        )
                        if not plan_only:
                            await self.coordinator.async_send_command(
                                "start_job", priority=Priority.USER
                            )
                        return
                    if await self._async_planning_step(
                        self.coordinator.async_plan_route(
                            operational_settings, priority=Priority.USER
                        )
                    ):
                        if not plan_only:
                            await self.coordinator.async_send_and_wait(
                                "start_job",
                                "zone_start_precent_t",
                                priority=Priority.USER,
                            )

            except COMMAND_EXCEPTIONS as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key=trans_key
                ) from exc
            finally:
                await self.coordinator.async_request_report_snapshot()

    async def async_dock(self) -> None:
        """Start docking."""
        trans_key = "pause_failed"

        await self.coordinator.async_start_report_stream()
        charge_state = self.rpt_dev_status.charge_state
        mode = self.rpt_dev_status.sys_status
        if mode is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_not_ready"
            )

        if charge_state == 0 and mode in (
            WorkMode.MODE_WORKING,
            WorkMode.MODE_PAUSE,
            WorkMode.MODE_READY,
            WorkMode.MODE_RETURNING,
        ):
            try:
                if mode == WorkMode.MODE_WORKING:
                    trans_key = "pause_failed"
                    await self.coordinator.async_send_command(
                        "pause_execute_task", priority=Priority.USER
                    )

                if mode == WorkMode.MODE_RETURNING:
                    trans_key = "dock_cancel_failed"
                    await self.coordinator.async_send_command(
                        "cancel_return_to_dock", priority=Priority.USER
                    )
                else:
                    trans_key = "dock_failed"
                    await self.coordinator.async_send_command(
                        "return_to_dock", priority=Priority.USER
                    )
            except COMMAND_EXCEPTIONS as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key=trans_key
                ) from exc
            finally:
                await self.coordinator.async_request_report_snapshot()

    async def async_pause(self) -> None:
        """Pause mower."""
        trans_key = "pause_failed"

        await self.coordinator.async_ensure_fresh_state()
        mode = self.rpt_dev_status.sys_status
        if mode is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_not_ready"
            )

        if mode in (
            WorkMode.MODE_WORKING,
            WorkMode.MODE_RETURNING,
        ):
            try:
                if mode == WorkMode.MODE_WORKING:
                    trans_key = "pause_failed"
                    await self.coordinator.async_send_command(
                        "pause_execute_task", priority=Priority.USER
                    )
                if mode == WorkMode.MODE_RETURNING:
                    trans_key = "dock_cancel_failed"
                    await self.coordinator.async_send_command(
                        "cancel_return_to_dock", priority=Priority.USER
                    )
            except COMMAND_EXCEPTIONS as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key=trans_key
                ) from exc
            finally:
                await self.coordinator.async_request_report_snapshot()

    async def async_cancel(self) -> None:
        """Cancel Job."""
        await self.coordinator.async_ensure_fresh_state()
        mode = self.rpt_dev_status.sys_status
        if mode is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_not_ready"
            )

        if mode in (
            WorkMode.MODE_PAUSE,
            WorkMode.MODE_WORKING,
            WorkMode.MODE_RETURNING,
        ):
            try:
                await self._async_end_job(mode)
            finally:
                await self.coordinator.async_request_report_snapshot()

    @staticmethod
    async def _async_planning_step(step: Awaitable[object]) -> object:
        """Await a route planning step, carrying on if only its reply went missing.

        The planning replies often go unmatched (#848) although the mower took the
        command, so start_job still has to follow; every other failure propagates.
        """
        try:
            return await step
        except HomeAssistantError as exc:
            if exc.translation_key != "command_unconfirmed":
                raise
            LOGGER.debug("Route planning reply unconfirmed, continuing: %s", exc)
            return True

    async def _async_end_job(self, mode: int) -> None:
        """Stop the mower moving, then send cancel_job once it reports PAUSE."""
        trans_key = "pause_failed"
        try:
            if mode == WorkMode.MODE_WORKING:
                await self.coordinator.async_send_command(
                    "pause_execute_task", priority=Priority.USER
                )
                mode = await self._async_wait_for_mode(WorkMode.MODE_PAUSE)
            elif mode == WorkMode.MODE_RETURNING:
                trans_key = "dock_failed"
                await self.coordinator.async_send_command(
                    "cancel_return_to_dock", priority=Priority.USER
                )
                mode = await self._async_wait_for_mode(
                    WorkMode.MODE_PAUSE, WorkMode.MODE_READY
                )

            if mode == WorkMode.MODE_PAUSE:
                trans_key = "pause_failed"
                await self.coordinator.async_send_command(
                    "cancel_job", priority=Priority.USER
                )
        except COMMAND_EXCEPTIONS as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=trans_key
            ) from exc

    async def _async_wait_for_mode(self, *modes: WorkMode) -> WorkMode:
        """Return the first of *modes* the mower reports."""
        return await self._async_wait_until(
            lambda: (
                WorkMode(mode)
                if (mode := self.rpt_dev_status.sys_status) in modes
                else None
            )
        )

    async def _async_wait_until[T](self, read: Callable[[], T | None]) -> T:
        """Return the first value *read* gives that is not None, re-read on each report.

        A one-shot snapshot is skipped shortly after the last report, so a report
        stream is opened for the wait. Raises TimeoutError after ``_MODE_WAIT_TIMEOUT``.
        """
        reached: asyncio.Future[T] = asyncio.get_running_loop().create_future()

        @callback
        def _check() -> None:
            if not reached.done() and (value := read()) is not None:
                reached.set_result(value)

        remove_listener = self.coordinator.async_add_listener(_check)
        try:
            _check()
            if not reached.done():
                await self.coordinator.async_start_report_stream(
                    _MODE_WAIT_TIMEOUT * 1000
                )
            async with asyncio.timeout(_MODE_WAIT_TIMEOUT):
                return await reached
        finally:
            remove_listener()

    async def async_modify_running_job(self, **kwargs: Any) -> None:
        """Change settings on the job already running, without re-planning it."""
        await self.coordinator.async_modify_running_job(**kwargs)

    async def async_start_stop_blades(self, **kwargs: Any) -> None:
        """Start/Stop Blades."""
        await self.coordinator.async_start_stop_blades(**kwargs)

    async def async_set_non_work_hours(self, **kwargs: Any) -> None:
        """Set Non Work Hours."""
        start_time: time = kwargs["start_time"]
        end_time: time = kwargs["end_time"]

        await self.coordinator.async_set_non_work_hours(
            start_time=start_time.strftime("%H:%M"), end_time=end_time.strftime("%H:%M")
        )

    async def async_reset_blade_time(self) -> None:
        """Reset blade used time to zero."""
        if DeviceType.is_luba1(self.coordinator.device_name):
            return
        await self.coordinator.async_reset_blade_time()

    async def async_set_blade_warning_time(self, hours: int) -> None:
        """Set blade replacement warning threshold in hours."""
        if DeviceType.is_luba1(self.coordinator.device_name):
            return
        await self.coordinator.async_set_blade_warning_time(hours=hours)

    async def async_set_blade_height(self, height: int) -> None:
        """Send a blade height directly to the mower's cutter motor."""
        await self.coordinator.async_blade_height(height)

    def _assert_grass_collection(self) -> None:
        """Reject the dump-point services on mowers that take no collector."""
        if not supports_grass_collection(self.coordinator.device_name):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="grass_collection_unsupported",
            )

    async def async_start_dump_point_setup(self) -> None:
        """Put the mower into grass-collection point setup mode."""
        self._assert_grass_collection()
        await self.coordinator.async_enter_dump_point_setup()

    async def async_add_dump_point(self) -> None:
        """Record a grass-collection point at the mower's current position."""
        self._assert_grass_collection()
        await self.coordinator.async_add_dump_point()

    async def async_undo_dump_point(self) -> None:
        """Undo the last recorded grass-collection point."""
        self._assert_grass_collection()
        await self.coordinator.async_revoke_dump_point()

    async def async_finish_dump_point_setup(self) -> None:
        """Save the recorded grass-collection points and leave setup mode."""
        self._assert_grass_collection()
        await self.coordinator.async_exit_dump_point_setup()

    async def async_finish_outside_dump_point(self) -> None:
        """Finish a collection point recorded outside the mowing area."""
        self._assert_grass_collection()
        await self.coordinator.async_finish_outside_dump_point()

    async def async_added_to_hass(self) -> None:
        """Register callbacks and verify device linkage after HA setup."""
        await super().async_added_to_hass()

        # Ensure the entity is actually linked to a device
        if not self.coordinator.device_name:
            return

        device_registry = dr.async_get(self.hass)

        device = device_registry.async_get_device(
            identifiers={(DOMAIN, self.coordinator.device_name)}
        )

        if device:
            for conn_type, value in device.connections:
                if conn_type == dr.CONNECTION_NETWORK_MAC:
                    self.coordinator.data.mower_state.wifi_mac = value
                elif conn_type == dr.CONNECTION_BLUETOOTH:
                    self.coordinator.data.mower_state.ble_mac = value
