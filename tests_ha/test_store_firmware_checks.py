"""The firmware-check timestamps survive a restart, and older stores migrate.

Keeping this in memory only would make every Home Assistant restart look like
"never checked", so the first reconnect after each restart would fire a cloud
call for every device on the account.
"""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.mammotion.config import (
    STORE_MINOR_VERSION,
    STORE_VERSION,
    MammotionConfigStore,
)

_DEVICE = "Luba-VS123456"


async def test_a_recorded_check_is_read_back_after_a_restart(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """The whole point: a week has to be measured across restarts."""
    when = dt_util.utcnow()
    store = MammotionConfigStore(hass, "entry1")
    await store.async_load_device_data()
    await store.async_set_firmware_checked(_DEVICE, when)

    reloaded = MammotionConfigStore(hass, "entry1")
    await reloaded.async_load_device_data()

    assert reloaded.firmware_checked_at(_DEVICE) == when


async def test_an_unchecked_device_reads_back_as_none(hass: HomeAssistant) -> None:
    """A device nobody has checked must look overdue, not fresh."""
    store = MammotionConfigStore(hass, "entry1")
    await store.async_load_device_data()

    assert store.firmware_checked_at(_DEVICE) is None


async def test_a_v1_2_store_migrates_without_losing_anything(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """The section was added after devices and transports; both must survive."""
    hass_storage["mammotion.entry1"] = {
        "version": 1,
        "minor_version": 2,
        "key": "mammotion.entry1",
        "data": {
            "devices": {_DEVICE: {"name": _DEVICE}},
            "transports": {_DEVICE: {"bluetooth_enabled": False}},
        },
    }
    store = MammotionConfigStore(hass, "entry1")
    await store.async_load_device_data()

    assert store.device_data == {_DEVICE: {"name": _DEVICE}}
    assert store.transport_enabled(_DEVICE, "bluetooth_enabled") is False
    assert store.firmware_checks == {}


async def test_a_v1_1_store_still_migrates_all_the_way(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """The oldest layout was a bare device map; both migration steps must run."""
    hass_storage["mammotion.entry1"] = {
        "version": 1,
        "minor_version": 1,
        "key": "mammotion.entry1",
        "data": {_DEVICE: {"name": _DEVICE}},
    }
    store = MammotionConfigStore(hass, "entry1")
    await store.async_load_device_data()

    assert store.device_data == {_DEVICE: {"name": _DEVICE}}
    assert store.firmware_checks == {}


async def test_removing_a_device_drops_its_check(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """A re-added device should not inherit the old one's check time."""
    store = MammotionConfigStore(hass, "entry1")
    await store.async_load_device_data()
    await store.async_set_firmware_checked(_DEVICE, dt_util.utcnow())

    await store.async_remove_device(_DEVICE)

    assert store.firmware_checked_at(_DEVICE) is None


async def test_the_written_file_carries_the_current_version(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """A store written at the old version would migrate again on every load."""
    store = MammotionConfigStore(hass, "entry1")
    await store.async_load_device_data()
    await store.async_set_firmware_checked(
        _DEVICE, dt_util.utcnow() - timedelta(days=9)
    )

    written = hass_storage["mammotion.entry1"]
    assert written["version"] == STORE_VERSION
    assert written["minor_version"] == STORE_MINOR_VERSION
    assert set(written["data"]) == {"devices", "transports", "firmware_checks"}
