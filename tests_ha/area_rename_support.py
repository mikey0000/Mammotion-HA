"""A Luba 2 whose area the user renamed, wired from a real handle to real entities.

``make_area_rename_rig`` builds a real ``DeviceHandle`` and a report coordinator
built by its own ``__init__`` with its push subscriptions in place; only the client
is a spec'd stand-in.  ``ECHO`` is the frame the mower sent back after the rename;
``echo_of`` is that frame carrying another name.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from unittest.mock import create_autospec

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import AreaHashNameList, FrameList
from pymammotion.device.handle import DeviceHandle
from pymammotion.proto import LubaMsg
from pymammotion.transport.base import TransportType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)
from remote_drive_support import make_mower_data, make_runtime_data
from user_command_support import make_cloud_handle

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

MOWER = "Luba-VS563L6H"
AREA_HASH = 7679116196473055915
#: The device's name before the rename; the trailing space is the mower's own.
DEVICE_AREA_NAME = "Back backyard "
#: The ``toappMapNameMsg`` RESP the mower sent after the rename (2026-10-02 log).
ECHO = bytes.fromhex(
    "08f00110011807200228850430015a3ed2033b080111abaab9b77eb3916a1a1241726561206261"
    "636b206261636b796172642a1a5649666e73674951436d48716e34495858576b51303030303030"
)
ECHOED_NAME = "Area back backyard"
#: What HA pushes for the label ``ECHOED_NAME``: the area word is HA's, not the mower's.
STRIPPED_NAME = "back backyard"


def echo_of(name: str) -> bytes:
    """Return ``ECHO`` as the mower would send it for an area renamed to *name*."""
    msg = LubaMsg.parse(ECHO)
    msg.nav.toapp_map_name_msg.name = name
    return bytes(msg)


@dataclass
class AreaRenameRig:
    """The handle, the coordinator over it and the client stand-in behind both."""

    coordinator: MammotionReportUpdateCoordinator
    handle: DeviceHandle
    manager: MammotionClient
    entry: MockConfigEntry

    async def receive_echo(self, frame: bytes = ECHO) -> None:
        """Deliver the mower's rename echo the way a cloud frame arrives."""
        await self.handle.on_raw_message(frame, TransportType.CLOUD_MAMMOTION)


async def make_area_rename_rig(
    hass: HomeAssistant, device: MowingDevice | None = None
) -> AreaRenameRig:
    """Build the rig over *device* (default: a map holding only the renamed area).

    The caller shuts the coordinator down.
    """
    if device is None:
        device = MowingDevice()
        device.map.area = {AREA_HASH: FrameList()}
        device.map.area_name = [AreaHashNameList(name=DEVICE_AREA_NAME, hash=AREA_HASH)]
    handle = make_cloud_handle(MOWER, device, reported_offline=False)
    await handle.stop_polling()
    manager = create_autospec(MammotionClient, instance=True)
    manager.get_device_by_name.return_value = device
    manager.mower.return_value = handle
    manager.to_cache.return_value = {}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MOWER)
    entry.add_to_hass(hass)
    coordinator = MammotionReportUpdateCoordinator(
        hass, entry, _create_ble_only_device(MOWER), manager, unique_name=MOWER
    )
    coordinator.async_set_updated_data(handle.state_machine.current.raw)
    # Wires the push subscriptions, as the background bring-up does.
    await coordinator._async_setup()
    entry.runtime_data = make_runtime_data(make_mower_data(coordinator))
    return AreaRenameRig(coordinator, handle, manager, entry)


def set_area_name_pushes(manager: MammotionClient) -> list[str]:
    """Return the area names pushed to the mower, in order."""
    return [
        call.kwargs["name"]
        for call in manager.send_command_and_wait.await_args_list
        if call.args[1] == "set_area_name"
    ]


async def add_synced_sensors(
    hass: HomeAssistant,
    rig: AreaRenameRig,
    sync: Callable[[Callable[[list[Entity]], None]], None],
) -> list[Entity]:
    """Register what *sync* adds on a translated sensor platform; re-sync on updates.

    Entities a later sync adds are collected but not registered.  The caller
    shuts the coordinator down, which drops the listener.
    """
    platform = MockEntityPlatform(hass, domain="sensor", platform_name=DOMAIN)
    platform.config_entry = rig.entry
    await platform.platform_data.async_load_translations()
    added: list[Entity] = []
    sync(added.extend)
    await platform.async_add_entities(added)
    rig.coordinator.async_add_listener(partial(sync, added.extend))
    return added
