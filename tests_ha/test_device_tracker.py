"""The mower's GPS tracker is written to the state machine only when it changes.

The real entry is set up with only the ``device_tracker`` platform, over a spec'd
client whose device record the tracker reads, and snapshots arrive through the
report coordinator's ``async_set_updated_data`` as pushed frames do at runtime.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import create_autospec, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_LATITUDE, EVENT_STATE_CHANGED, Platform
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceHandle
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

import custom_components.mammotion as mammotion_init
from custom_components.mammotion.const import ATTR_DIRECTION, CONF_BLE_DEVICES, DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_MOWER = "Luba-VS123456"
_MOWER_MAC = "aa:bb:cc:dd:ee:ff"
#: How often a docked mower pushes a report frame.
_PUSH_INTERVAL = timedelta(seconds=15)


def _client() -> MammotionClient:
    """Return a client stand-in that keeps the one device record the tracker reads.

    ``restore_device`` replaces that record, as the real handle does.
    """
    records: dict[str, MowingDevice] = {}
    handle = create_autospec(DeviceHandle, instance=True)
    handle.restore_device.side_effect = lambda record: records.__setitem__(
        _MOWER, record
    )
    client = create_autospec(MammotionClient, instance=True)
    client.add_ble_only_device.side_effect = (
        lambda *, device_name, initial_device, **_kwargs: records.setdefault(
            device_name, initial_device
        )
    )
    client.get_device_by_name.side_effect = records.get
    client.mower.return_value = handle
    return client


@dataclass
class _Tracker:
    """The loaded tracker entity and the coordinator that pushes to it."""

    hass: HomeAssistant
    entity_id: str
    coordinator: MammotionReportUpdateCoordinator

    async def push(
        self,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        orientation: int | None = None,
    ) -> None:
        """Deliver a snapshot with the given fields changed and the rest as they were."""
        record = self.coordinator.manager.get_device_by_name(_MOWER)
        assert record is not None
        if latitude is not None:
            record.location.device.latitude = latitude
        if longitude is not None:
            record.location.device.longitude = longitude
        if orientation is not None:
            record.location.orientation = orientation
        self.coordinator.async_set_updated_data(record)
        await self.hass.async_block_till_done()

    def changes(
        self, events: list[Event[EventStateChangedData]]
    ) -> list[EventStateChangedData]:
        """Return the tracker's own ``state_changed`` payloads from *events*."""
        return [
            event.data for event in events if event.data["entity_id"] == self.entity_id
        ]


@pytest.fixture
async def tracker(hass: HomeAssistant) -> AsyncIterator[_Tracker]:
    """Set the entry up for real with only the tracker platform and no device I/O."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_MOWER_MAC,
        data={CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC}},
    )
    entry.add_to_hass(hass)
    with (
        patch.object(mammotion_init, "MammotionClient", autospec=True) as client_cls,
        patch.object(mammotion_init, "PLATFORMS", [Platform.DEVICE_TRACKER]),
        patch.object(mammotion_init, "_async_bring_up_devices", autospec=True),
    ):
        client_cls.return_value = _client()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        (registered,) = er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        yield _Tracker(
            hass,
            registered.entity_id,
            entry.runtime_data.mowers[0].reporting_coordinator,
        )
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.regression
async def test_an_unchanged_snapshot_is_not_written_again(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, tracker: _Tracker
) -> None:
    """A docked mower's repeated frames must not count as state changes.

    ``TrackerEntity.force_update`` is a property returning ``not should_poll``
    (True for a push tracker) that never reads ``_attr_force_update``, so the
    class attribute meant to turn it off did nothing.  Every pushed frame, about
    one every 15 s while docked, fired ``state_changed``, reset ``last_changed``
    and cost a recorder row although neither the state nor an attribute had
    changed.
    """
    await tracker.push(latitude=0.5, longitude=0.5, orientation=90)
    written = hass.states.get(tracker.entity_id)
    assert written is not None
    last_changed, last_updated = written.last_changed, written.last_updated
    events = async_capture_events(hass, EVENT_STATE_CHANGED)
    freezer.tick(_PUSH_INTERVAL)

    await tracker.push()

    state = hass.states.get(tracker.entity_id)
    assert state is not None
    assert tracker.changes(events) == []
    assert state.last_changed == last_changed
    assert state.last_updated == last_updated


@pytest.mark.parametrize(
    ("latitude", "orientation", "written_attribute"),
    [
        pytest.param(0.5001, None, (ATTR_LATITUDE, 0.5001), id="moved"),
        pytest.param(None, 180, (ATTR_DIRECTION, 180), id="turned"),
    ],
)
async def test_a_moved_or_turned_mower_is_written(
    freezer: FrozenDateTimeFactory,
    tracker: _Tracker,
    latitude: float | None,
    orientation: int | None,
    written_attribute: tuple[str, float],
) -> None:
    """Only the redundant writes go; a new position or heading still reaches HA."""
    await tracker.push(latitude=0.5, longitude=0.5, orientation=90)
    events = async_capture_events(tracker.hass, EVENT_STATE_CHANGED)
    freezer.tick(_PUSH_INTERVAL)

    await tracker.push(latitude=latitude, orientation=orientation)

    (written,) = tracker.changes(events)
    old_state, new_state = written["old_state"], written["new_state"]
    assert old_state is not None
    assert new_state is not None
    attribute, value = written_attribute
    assert new_state.attributes[attribute] == value
    assert new_state.last_updated == old_state.last_updated + _PUSH_INTERVAL
