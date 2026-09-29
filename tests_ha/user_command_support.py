"""Shared scaffolding for the user-command tests: a real coordinator without HA.

The coordinator class is the shipped one, built with ``object.__new__`` so no Home
Assistant is needed; the client is a spec'd stand-in.  ``make_cloud_handle`` gives
a real ``DeviceHandle`` whose only transport is cloud MQTT, so the offline gate the
coordinator consults is pymammotion's own.
"""

from types import SimpleNamespace
from unittest.mock import create_autospec

from pymammotion.client import MammotionClient
from pymammotion.data.model.device import Device
from pymammotion.device.handle import DeviceHandle
from pymammotion.transport.base import TransportAvailability, TransportType
from pymammotion.transport.cloud import CloudTransport


def make_cloud_handle(
    device_name: str,
    device: Device,
    *,
    reported_offline: bool,
    cloud_usable: bool = True,
) -> DeviceHandle:
    """Return a real handle whose only transport is cloud MQTT."""
    mqtt = create_autospec(CloudTransport, instance=True)
    mqtt.transport_type = TransportType.CLOUD_ALIYUN
    mqtt.is_usable = cloud_usable
    mqtt.is_connected = True
    handle = DeviceHandle("dev-1", device_name, device, mqtt_transport=mqtt)
    handle.update_availability(
        TransportType.CLOUD_ALIYUN,
        TransportAvailability.CONNECTED,
        mqtt_reported_offline=reported_offline,
    )
    return handle


def make_coordinator[CoordinatorT](
    coordinator_cls: type[CoordinatorT],
    device: Device,
    *,
    device_name: str,
    handle: DeviceHandle | None = None,
) -> CoordinatorT:
    """Build *coordinator_cls* around *device* (marked online) and a spec'd client.

    With *handle*, the client's ``mower`` lookup returns it.
    """
    coordinator = object.__new__(coordinator_cls)
    coordinator.device_name = device_name
    coordinator.update_failures = 0
    coordinator._bluetooth_enabled = False
    device.online = True
    manager = create_autospec(MammotionClient, instance=True)
    manager.get_device_by_name.return_value = device
    if handle is not None:
        manager.mower.return_value = handle
    coordinator.manager = manager
    coordinator.data = device
    coordinator.config_entry = SimpleNamespace(options={})
    coordinator._listeners = {}
    coordinator._subscriptions = []
    coordinator._remote_drive_subscription = None
    coordinator._remote_drive_listeners = []
    coordinator.remote_drive_last_event = None
    return coordinator
