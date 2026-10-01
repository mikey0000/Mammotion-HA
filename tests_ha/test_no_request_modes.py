"""The coordinator's poll gate uses pymammotion's NO_REQUEST_MODES, not a copy.

The integration kept its own eight-mode tuple, which missed the five modes the
library added later, so Home Assistant polled a mower that was backing up or
restoring its map, or asleep (#917 family).
"""

import pytest
from pymammotion.utility.constant import WorkMode, poll_policy

from custom_components.mammotion import const, coordinator


@pytest.mark.regression
def test_the_poll_gate_is_the_librarys_table() -> None:
    """One table, so a mode the library adds is skipped here too."""
    assert coordinator.NO_REQUEST_MODES is poll_policy.NO_REQUEST_MODES
    assert not hasattr(const, "NO_REQUEST_MODES")


@pytest.mark.regression
@pytest.mark.parametrize(
    "mode",
    [
        WorkMode.MODE_AUTO_ERASER_DRAW,
        WorkMode.MODE_CORRIDOR_DRAW,
        WorkMode.MODE_SLEEPING,
        WorkMode.MODE_BACKING_UP,
        WorkMode.MODE_RECOVERY,
    ],
)
def test_the_modes_the_copy_missed_are_not_polled(mode: WorkMode) -> None:
    """The five modes the integration's copy had drifted away from."""
    assert mode in coordinator.NO_REQUEST_MODES
