"""Bluetooth setup accepts a Spino, and routes it away from the mower path.

A pool cleaner could previously only be added through a cloud account:
``DEVICE_SUPPORT`` gated BLE discovery, manual selection and registration on
``("Luba", "Yuka")``.  Widening that alone is not enough — every BLE-only
device was turned into a synthetic mower record, so a Spino would have been
given mower coordinators and a lawn_mower entity.
"""

import pytest
from pymammotion.utility.device_type import DeviceType

from custom_components.mammotion.const import (
    BLE_SUPPORT,
    DEVICE_SUPPORT,
    POOL_CLEANER_SUPPORT,
)

_SPINO = "Spino-E1C36JT4"
_MOWER = "Luba-VS123456"
_PILE = "SDPX123456"


@pytest.mark.parametrize("name", [_SPINO, _MOWER])
def test_both_kinds_can_be_set_up_over_bluetooth(name: str) -> None:
    """A Spino was refused here; only mowers passed the gate."""
    assert name.startswith(BLE_SUPPORT)


def test_the_charging_pile_is_still_refused() -> None:
    """SDPX is a PC210 dock, not a cleaner, and has none of the state to show."""
    assert not _PILE.startswith(BLE_SUPPORT)


def test_a_spino_is_not_treated_as_a_mower() -> None:
    """DEVICE_SUPPORT still answers "is this a mower" on the cloud path."""
    assert not _SPINO.startswith(DEVICE_SUPPORT)
    assert _SPINO.startswith(POOL_CLEANER_SUPPORT)


def test_a_mower_is_not_routed_to_the_pool_path() -> None:
    """The split has to cut both ways or a mower loses its own coordinators."""
    assert not _MOWER.startswith(POOL_CLEANER_SUPPORT)


def test_the_variant_resolves_without_a_cloud_product_key() -> None:
    """BLE-only devices have no product_key, so the name has to carry it."""
    assert DeviceType.value_of_str(_SPINO, "") is DeviceType.SWIMMINGPOOL_E1
