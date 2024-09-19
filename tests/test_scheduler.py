import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.mammotion.scheduler import MammotionScheduler
from custom_components.mammotion.coordinator import MammotionDataUpdateCoordinator


class TestMammotionScheduler(unittest.TestCase):
    def setUp(self):
        self.hass = MagicMock(spec=HomeAssistant)
        self.coordinator = MagicMock(spec=MammotionDataUpdateCoordinator)
        self.scheduler = MammotionScheduler(self.hass, self.coordinator)

    def test_add_schedule(self):
        start_time = utcnow() + timedelta(seconds=1)
        end_time = start_time + timedelta(minutes=30)
        task = "start_mowing"
        kwargs = {"area": "front_yard"}

        self.scheduler.add_schedule(start_time, end_time, task, **kwargs)
        self.assertEqual(len(self.scheduler.schedules), 1)
        self.assertEqual(self.scheduler.schedules[0]["task"], task)
        self.assertEqual(self.scheduler.schedules[0]["kwargs"], kwargs)

    def test_remove_schedule(self):
        start_time = utcnow() + timedelta(seconds=1)
        end_time = start_time + timedelta(minutes=30)
        task = "start_mowing"
        kwargs = {"area": "front_yard"}

        self.scheduler.add_schedule(start_time, end_time, task, **kwargs)
        self.scheduler.remove_schedule(0)
        self.assertEqual(len(self.scheduler.schedules), 0)

    def test_modify_schedule(self):
        start_time = utcnow() + timedelta(seconds=1)
        end_time = start_time + timedelta(minutes=30)
        task = "start_mowing"
        kwargs = {"area": "front_yard"}

        self.scheduler.add_schedule(start_time, end_time, task, **kwargs)
        new_start_time = start_time + timedelta(minutes=10)
        new_end_time = end_time + timedelta(minutes=10)
        new_task = "stop_mowing"
        new_kwargs = {"area": "back_yard"}

        self.scheduler.modify_schedule(0, new_start_time, new_end_time, new_task, **new_kwargs)
        self.assertEqual(self.scheduler.schedules[0]["start_time"], new_start_time)
        self.assertEqual(self.scheduler.schedules[0]["end_time"], new_end_time)
        self.assertEqual(self.scheduler.schedules[0]["task"], new_task)
        self.assertEqual(self.scheduler.schedules[0]["kwargs"], new_kwargs)

    @patch("custom_components.mammotion.scheduler.async_track_point_in_utc_time")
    def test_schedule_task(self, mock_async_track_point_in_utc_time):
        start_time = utcnow() + timedelta(seconds=1)
        end_time = start_time + timedelta(minutes=30)
        task = "start_mowing"
        kwargs = {"area": "front_yard"}

        self.scheduler.add_schedule(start_time, end_time, task, **kwargs)
        self.assertEqual(mock_async_track_point_in_utc_time.call_count, 2)
        self.assertEqual(mock_async_track_point_in_utc_time.call_args_list[0][0][1].__name__, "start_task")
        self.assertEqual(mock_async_track_point_in_utc_time.call_args_list[1][0][1].__name__, "stop_task")


if __name__ == "__main__":
    unittest.main()
