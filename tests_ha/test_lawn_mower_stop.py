"""The lawn_mower stop action and idle activity added in Home Assistant 2026.10.

Stop cancels the running job and leaves the mower where it is.  A mower that is
ready but off the dock has no job to resume, so it is idle rather than paused.
Older Home Assistant has neither, so the entity falls back to paused and does not
advertise stop.
"""

from unittest.mock import patch

import pytest
from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntityFeature,
)
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.utility.constant.device_constant import WorkMode

from custom_components.mammotion import lawn_mower
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


def test_the_version_gate_matches_what_home_assistant_provides() -> None:
    """A wrong threshold would reference enum members the running Home Assistant lacks."""
    assert (
        hasattr(LawnMowerActivity, "IDLE") and hasattr(LawnMowerEntityFeature, "STOP")
    ) == lawn_mower.HAS_STOP_AND_IDLE


def test_older_home_assistant_reports_ready_off_the_dock_as_paused() -> None:
    """Before 2026.10 there is no idle activity to report."""
    with patch.object(lawn_mower, "HAS_STOP_AND_IDLE", False):
        activity = _entity(WorkMode.MODE_READY).activity

    assert activity == LawnMowerActivity.PAUSED


def test_older_home_assistant_does_not_advertise_stop() -> None:
    """Before 2026.10 the stop feature flag does not exist."""
    with patch.object(lawn_mower, "HAS_STOP_AND_IDLE", False):
        entity = _entity(WorkMode.MODE_READY)

    assert not entity.supported_features & LawnMowerEntityFeature.STOP
    assert entity.supported_features & LawnMowerEntityFeature.PAUSE
