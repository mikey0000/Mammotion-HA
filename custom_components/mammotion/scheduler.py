"""Scheduler for the Mammotion integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util.dt import utcnow

from .const import DOMAIN
from .coordinator import MammotionDataUpdateCoordinator


class MammotionScheduler:
    """Class to handle scheduling tasks for the Mammotion mower."""

    def __init__(self, hass: HomeAssistant, coordinator: MammotionDataUpdateCoordinator) -> None:
        """Initialize the scheduler."""
        self.hass = hass
        self.coordinator = coordinator
        self.schedules = []

    def add_schedule(self, start_time: datetime, end_time: datetime, task: str, **kwargs: Any) -> None:
        """Add a new schedule."""
        schedule = {
            "start_time": start_time,
            "end_time": end_time,
            "task": task,
            "kwargs": kwargs,
        }
        self.schedules.append(schedule)
        self._schedule_task(schedule)

    def remove_schedule(self, schedule_id: int) -> None:
        """Remove a schedule."""
        if 0 <= schedule_id < len(self.schedules):
            self.schedules.pop(schedule_id)

    def _schedule_task(self, schedule: dict) -> None:
        """Schedule a task."""
        start_time = schedule["start_time"]
        end_time = schedule["end_time"]
        task = schedule["task"]
        kwargs = schedule["kwargs"]

        @callback
        def start_task(now: datetime) -> None:
            """Start the scheduled task."""
            if task == "start_mowing":
                self.hass.async_create_task(self.coordinator.async_start_mowing(**kwargs))
            elif task == "stop_mowing":
                self.hass.async_create_task(self.coordinator.async_dock())

        @callback
        def stop_task(now: datetime) -> None:
            """Stop the scheduled task."""
            self.hass.async_create_task(self.coordinator.async_dock())

        async_track_point_in_utc_time(self.hass, start_task, start_time)
        async_track_point_in_utc_time(self.hass, stop_task, end_time)

    def modify_schedule(self, schedule_id: int, start_time: datetime = None, end_time: datetime = None, task: str = None, **kwargs: Any) -> None:
        """Modify an existing schedule."""
        if 0 <= schedule_id < len(self.schedules):
            schedule = self.schedules[schedule_id]
            if start_time:
                schedule["start_time"] = start_time
            if end_time:
                schedule["end_time"] = end_time
            if task:
                schedule["task"] = task
            if kwargs:
                schedule["kwargs"] = kwargs
            self._schedule_task(schedule)

    def get_schedules(self) -> list[dict]:
        """Get all schedules."""
        return self.schedules
