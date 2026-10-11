"""The lawn_mower stop action and idle activity added in Home Assistant 2026.10.

Stop cancels the running job and leaves the mower where it is.  A mower that is
ready but off the dock has no job to resume, so it is idle rather than paused.
"""

import pytest
from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntityFeature,
)
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.utility.constant.device_constant import WorkMode

from custom_components.mammotion.lawn_mower import MammotionLawnMowerEntity
from tests_ha.job_service_support import make_mower_entity


def _entity(mode: WorkMode, charge_state: int = 0) -> MammotionLawnMowerEntity:
    """Build the real lawn mower entity over a mower in the given work mode."""
    entity = make_mower_entity(OperationSettings(), mode=mode)
    entity.coordinator.data.report_data.dev.charge_state = charge_state
    return entity


@pytest.mark.parametrize(
    ("mode", "charge_state", "expected"),
    [
        (WorkMode.MODE_READY, 0, LawnMowerActivity.IDLE),
        (WorkMode.MODE_READY, 1, LawnMowerActivity.DOCKED),
        (WorkMode.MODE_PAUSE, 0, LawnMowerActivity.PAUSED),
        (WorkMode.MODE_CHARGING_PAUSE, 1, LawnMowerActivity.PAUSED),
        (WorkMode.MODE_WORKING, 0, LawnMowerActivity.MOWING),
    ],
)
def test_activity_maps_the_work_mode(
    mode: WorkMode, charge_state: int, expected: LawnMowerActivity
) -> None:
    """Ready off the dock is the only mode that moves from paused to idle."""
    assert _entity(mode, charge_state).activity == expected


def test_stop_is_a_supported_feature() -> None:
    """Without the flag, Home Assistant hides the stop action for the mower."""
    entity = _entity(WorkMode.MODE_READY)
    assert entity.supported_features & LawnMowerEntityFeature.STOP
