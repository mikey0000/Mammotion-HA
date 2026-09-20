"""Bluetooth setup accepts a Spino, and routes it away from the mower path.

A pool cleaner could previously only be added through a cloud account:
``DEVICE_SUPPORT`` gated BLE discovery, manual selection and registration on
``("Luba", "Yuka")``.  Widening that alone is not enough — every BLE-only
device was turned into a synthetic mower record, so a Spino would have been
given mower coordinators and a lawn_mower entity.
"""

import json
from pathlib import Path

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
_MAMMOTION_SERVICE_UUID = "0000ffff-0000-1000-8000-00805f9b34fb"


def _manifest() -> dict:
    """Return the integration manifest, which is what HA actually matches on."""
    path = (
        Path(__file__).parent.parent
        / "custom_components"
        / "mammotion"
        / "manifest.json"
    )
    return json.loads(path.read_text())


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


def test_every_ble_supported_prefix_has_a_discovery_matcher() -> None:
    """Accepting a Spino in the flow is moot if HA never starts one.

    ``async_step_bluetooth`` was widened with ``BLE_SUPPORT`` but the manifest
    still listed only the two mowers, so a pool cleaner could be added by hand
    or over the cloud and never by discovery.
    """
    matchers = {matcher["local_name"] for matcher in _manifest()["bluetooth"]}
    assert matchers == {f"{prefix}-*" for prefix in BLE_SUPPORT}


def test_every_matcher_requires_the_mammotion_service_uuid() -> None:
    """A Spino-E1 was observed advertising 0000ffff, same as the mowers.

    Confirmed against a real cleaner (``Spino-E1C36JT4``, AC:EB:E6:8A:F4:FE) on
    a local adapter, so the cleaner is matched as narrowly as the mowers rather
    than on its name alone.  Two other Mammotion devices in range advertise the
    same UUID (an RTK base and ``RBSA1HCE96R``) and are held off by the name.
    """
    matchers = _manifest()["bluetooth"]
    assert {m["service_uuid"] for m in matchers} == {_MAMMOTION_SERVICE_UUID}
    assert all(m["connectable"] is True for m in matchers)
