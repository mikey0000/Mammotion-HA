"""Shared scaffolding for the device-removal tests: a client double and the set-up seams."""

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, create_autospec, patch

from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceHandle

import custom_components.mammotion as mammotion_init


def make_removal_client(devices: list[Device] | None = None) -> MagicMock:
    """Return a client whose account lists ``devices``, each with a mower record and a handle."""
    client = create_autospec(MammotionClient, instance=True)
    client.on_device_removed = None
    client.to_cache.return_value = {}
    client.aliyun_device_list = []
    client.mammotion_device_list = list(devices or [])
    records = {
        device.device_name: MowingDevice(name=device.device_name)
        for device in client.mammotion_device_list
    }
    client.get_device_by_name.side_effect = records.get
    handle = create_autospec(DeviceHandle, instance=True)
    handle.remote_drive = None
    client.mower.return_value = handle
    return client


@contextmanager
def patched_setup(
    client: MagicMock, *, logged_in: bool, platforms: bool = True
) -> Iterator[None]:
    """Hand ``client`` to every set-up, with the login answered and the device bring-up held back.

    The login and bring-up are the integration's own seams, autospecced so a
    signature change still breaks these tests: the real ones drive pymammotion's
    whole session and per-device connect, which the removal path does not touch.
    Without ``platforms`` no entity platform loads.
    """
    with (
        patch.object(mammotion_init, "MammotionClient", return_value=client),
        patch.object(
            mammotion_init,
            "_async_attempt_login",
            autospec=True,
            return_value=logged_in,
        ),
        patch.object(
            mammotion_init, "_async_bring_up_devices", autospec=True, return_value=None
        ),
        patch.object(
            mammotion_init,
            "PLATFORMS",
            mammotion_init.PLATFORMS if platforms else [],
        ),
    ):
        yield
