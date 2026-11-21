"""The map coordinator's periodic refresh syncs a stale map only while no job is active."""

import pytest
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import (
    NavGetCommData,
    NavGetHashListData,
    PathType,
)
from pymammotion.data.model.report_info import LocationData
from pymammotion.utility.constant.device_enums import WorkMode
from user_command_support import make_cloud_handle, make_coordinator

from custom_components.mammotion.coordinator import MammotionMapUpdateCoordinator

_NAME = "Luba-MS1000001"
_AREA = 4053101126662577264


def _map_coordinator(sys_status: int) -> MammotionMapUpdateCoordinator:
    """Return a map coordinator over a cloud mower in *sys_status* whose reported map differs from its cache."""
    device = MowingDevice(name=_NAME)
    device.map.update_root_hash_list(
        NavGetHashListData(
            pver=1, sub_cmd=0, total_frame=1, current_frame=1, data_couple=[_AREA]
        )
    )
    device.map.update(
        NavGetCommData(
            pver=1,
            action=8,
            type=PathType.AREA,
            hash=_AREA,
            total_frame=1,
            current_frame=1,
        )
    )
    device.report_data.locations = [
        LocationData(bol_hash=device.map.computed_bol_hash + 1)
    ]
    device.report_data.dev.sys_status = sys_status
    device.location.RTK.latitude = device.location.dock.latitude = 0.7
    coordinator = make_coordinator(
        MammotionMapUpdateCoordinator,
        device,
        device_name=_NAME,
        handle=make_cloud_handle(_NAME, device, reported_offline=False),
    )
    coordinator._startup_reads_done = True
    return coordinator


@pytest.mark.regression
async def test_a_stale_map_is_not_synced_during_a_job() -> None:
    """A mower re-hashes its map as it mows; the hourly refresh fetched the whole map mid-job."""
    coordinator = _map_coordinator(WorkMode.MODE_WORKING)

    await coordinator._async_update_data()

    coordinator.manager.start_map_sync.assert_not_awaited()


async def test_a_stale_map_is_synced_once_no_job_is_active() -> None:
    """The refresh still catches up a stale map while the mower is idle."""
    coordinator = _map_coordinator(WorkMode.MODE_READY)

    await coordinator._async_update_data()

    coordinator.manager.start_map_sync.assert_awaited_once_with(_NAME)
