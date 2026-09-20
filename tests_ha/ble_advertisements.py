"""Feed BLE advertisements to the real Home Assistant bluetooth manager.

``pytest-homeassistant-custom-component`` ships the ``enable_bluetooth`` fixture
but not Home Assistant core's ``tests.components.bluetooth`` helpers, so the few
lines that build a ``BluetoothServiceInfoBleak`` and hand it to the manager's
advertisement callback live here instead of being repeated in each test module.

The advertisement deliberately carries none of the service UUIDs the manifest
matches on: the tests drive the discovery flow themselves, and a second flow
started automatically by the bluetooth integration would race them.
"""

import time
from itertools import count

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from habluetooth import BluetoothServiceInfoBleak
from homeassistant.components.bluetooth import async_get_advertisement_callback
from homeassistant.core import HomeAssistant

# Home Assistant ignores an advertisement whose name, manufacturer data, service
# data and service UUIDs all repeat the last one seen for that address, and its
# manager outlives a single test, so each one gets a payload of its own.  The
# UUID is made up, so no other integration matches on it either.
_COUNTER_UUID = "deadbeef-0000-1000-8000-00805f9b34fb"
_sequence = count(1)


def make_service_info(
    name: str, address: str, *, rssi: int = -60
) -> BluetoothServiceInfoBleak:
    """Build the advertisement Home Assistant would hand a discovery flow."""
    service_data = {_COUNTER_UUID: next(_sequence).to_bytes(4)}
    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data=service_data,
        service_uuids=[],
        rssi=rssi,
        tx_power=-127,
        platform_data=((),),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=rssi,
        manufacturer_data={},
        service_data=service_data,
        service_uuids=[],
        source="local",
        device=BLEDevice(address=address, name=name, details={}),
        advertisement=advertisement,
        connectable=True,
        time=time.monotonic(),
        tx_power=-127,
    )


def inject_advertisement(
    hass: HomeAssistant, name: str, address: str, *, rssi: int = -60
) -> BluetoothServiceInfoBleak:
    """Make *name* discoverable at *address* and return its service info."""
    service_info = make_service_info(name, address, rssi=rssi)
    async_get_advertisement_callback(hass)(service_info)
    return service_info
