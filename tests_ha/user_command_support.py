"""Shared scaffolding for the user-command tests: a real coordinator without HA.

The coordinator class is the shipped one, built with ``object.__new__`` so no Home
Assistant is needed; the client is a spec'd stand-in.  ``make_cloud_handle`` gives
a real ``DeviceHandle`` whose only transport is cloud MQTT, so the offline gate the
coordinator consults is pymammotion's own.
"""

import time
from types import SimpleNamespace
from unittest.mock import create_autospec

from pymammotion.client import MammotionClient
from pymammotion.data.model.device import Device, MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.device.handle import DeviceHandle
from pymammotion.transport.base import TransportAvailability, TransportType
from pymammotion.transport.cloud import CloudTransport

from custom_components.mammotion.const import CONF_HAS_CLOUD_ACCOUNT
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

#: Handles whose poll loop may be running; ``conftest`` stops them after each test.
LIVE_HANDLES: list[DeviceHandle] = []


def make_cloud_handle(
    device_name: str,
    device: Device,
    *,
    reported_offline: bool,
    cloud_usable: bool = True,
    account_in_use: bool = False,
) -> DeviceHandle:
    """Return a real handle whose only transport is cloud MQTT.

    *account_in_use* is another session holding the Aliyun account lock, which
    leaves the transport unusable.
    """
    mqtt = create_autospec(CloudTransport, instance=True)
    mqtt.transport_type = TransportType.CLOUD_ALIYUN
    mqtt.is_usable = cloud_usable and not account_in_use
    mqtt.account_in_use = account_in_use
    mqtt.is_connected = True
    # Fresh traffic, so a poll loop the handle starts sleeps instead of polling.
    mqtt.last_received_monotonic = time.monotonic()
    handle = DeviceHandle("dev-1", device_name, device, mqtt_transport=mqtt)
    LIVE_HANDLES.append(handle)
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


def make_cloud_report_coordinator(
    device_name: str,
    *,
    cloud_usable: bool = True,
    reported_offline: bool = False,
    account_in_use: bool = False,
    operation_settings: OperationSettings | None = None,
) -> MammotionReportUpdateCoordinator:
    """Return a report coordinator over a cloud-only handle and no cloud account.

    Without a cloud account a credential failure skips the real login refresh.
    *operation_settings* are the planning values the config entities hold.
    """
    handle = make_cloud_handle(
        device_name,
        MowingDevice(),
        reported_offline=reported_offline,
        cloud_usable=cloud_usable,
        account_in_use=account_in_use,
    )
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator,
        MowingDevice(),
        device_name=device_name,
        handle=handle,
    )
    coordinator.config_entry = SimpleNamespace(
        options={}, data={CONF_HAS_CLOUD_ACCOUNT: False}
    )
    coordinator._operation_settings = operation_settings or OperationSettings()
    coordinator.unique_name = device_name
    return coordinator
