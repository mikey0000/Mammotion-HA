"""Which mowers get a camera entity at all.

The stream token is minted through the cloud, so a mower without an ``iot_id``
(BLE-only, no account) must get no camera — and neither must a Luba 1, which
has no camera hardware.  Against the real Home Assistant the platform's own
``DeviceType`` check decides that, and the camera services land on a real
service registry, so both halves are observed rather than asserted on mocks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.mammotion.camera import (
    MammotionWebRTCCamera,
    async_setup_entry,
)

# Real device names: the "VS" fourth character makes a Luba 2, which has a
# camera; a plain Luba-XXXXXX is a Luba 1, which does not.
_LUBA2_BLE = "Luba-VS00BLE"
_LUBA2_CLOUD = "Luba-VS00CLD"
_LUBA1_CLOUD = "Luba-AAAAAA"


def _mower(name: str, iot_id: str) -> MagicMock:
    """Build a mower entry; only the coordinator under the entity is a stand-in."""
    mower = MagicMock()
    mower.device.device_name = name
    mower.device.iot_id = iot_id
    mower.reporting_coordinator.unique_name = name
    mower.reporting_coordinator.device.device_name = name
    mower.reporting_coordinator.ice_servers = []
    mower.reporting_coordinator.async_check_stream_expiry = AsyncMock(
        return_value=(None, None)
    )
    return mower


def _entry(*mowers: MagicMock) -> MagicMock:
    entry = MagicMock()
    entry.runtime_data.mowers = list(mowers)
    return entry


@pytest.fixture
def added() -> list[MammotionWebRTCCamera]:
    """Collect whatever the platform hands to ``async_add_entities``."""
    return []


@pytest.fixture
def add_entities(added: list[MammotionWebRTCCamera]) -> MagicMock:
    """Stand in for the platform's add-entities callback."""
    return MagicMock(side_effect=added.extend)


async def test_ble_only_entry_creates_no_camera(
    hass: HomeAssistant, added: list[MammotionWebRTCCamera], add_entities: MagicMock
) -> None:
    """No cloud account means no token, so nothing is set up at all."""
    ble_mower = _mower(_LUBA2_BLE, "")

    await async_setup_entry(hass, _entry(ble_mower), add_entities)

    assert added == []
    assert not hass.services.has_service("mammotion", "start_video")
    ble_mower.reporting_coordinator.async_check_stream_expiry.assert_not_awaited()


async def test_luba1_gets_no_camera(
    hass: HomeAssistant, added: list[MammotionWebRTCCamera], add_entities: MagicMock
) -> None:
    """A Luba 1 has a cloud account but no camera hardware to stream from."""
    await async_setup_entry(hass, _entry(_mower(_LUBA1_CLOUD, "iot-123")), add_entities)

    assert added == []
    assert not hass.services.has_service("mammotion", "start_video")


async def test_only_cloud_mowers_get_cameras(
    hass: HomeAssistant, added: list[MammotionWebRTCCamera], add_entities: MagicMock
) -> None:
    """A cloud Luba 2 gets two cameras; the BLE-only mower gets none."""
    ble_mower = _mower(_LUBA2_BLE, "")
    cloud_mower = _mower(_LUBA2_CLOUD, "iot-123")

    await async_setup_entry(hass, _entry(ble_mower, cloud_mower), add_entities)

    assert [entity.coordinator for entity in added] == [
        cloud_mower.reporting_coordinator,
        cloud_mower.reporting_coordinator,
    ]
    assert all(isinstance(entity, MammotionWebRTCCamera) for entity in added)
    assert [entity.entity_description.key for entity in added] == [
        "webrtc_camera",
        "webrtc_camera_right",
    ]
    assert [entity._agora_handler._target_uid for entity in added] == [1, 2]
    assert hass.services.has_service("mammotion", "start_video")
    assert hass.services.has_service("mammotion", "stop_video")
