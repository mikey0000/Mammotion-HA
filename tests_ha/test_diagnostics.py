"""A diagnostics download has to carry every device on the entry.

A pool cleaner was simply absent: ``async_get_config_entry_diagnostics`` walked
``runtime_data.mowers`` and ``runtime_data.RTK`` and never ``.spino``, so the
one kind of device with its own coordinator and its own state model was the one
missing from the dump people attach to bug reports.
"""

from dataclasses import dataclass
from unittest.mock import MagicMock

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import (
    MowingDevice,
    PoolCleanerDevice,
    RTKBaseStationDevice,
)

from custom_components.mammotion.diagnostics import (
    async_get_config_entry_diagnostics,
)

_MOWER = "Luba-VS563L6H"
_RTK = "RTKBAU242721575"
_SPINO = "Spino-E1C36JT4"


@dataclass
class _Runtime:
    """Stands in for MammotionDevices; diagnostics reads these three lists."""

    mowers: list
    RTK: list
    spino: list


def _record(name: str, data: object, *, reporting: bool) -> MagicMock:
    """One entry in a runtime_data list, with the account record it carries."""
    record = MagicMock()
    record.name = name
    coordinator = MagicMock()
    coordinator.data = data
    if reporting:
        record.reporting_coordinator = coordinator
    else:
        record.coordinator = coordinator
    record.device.to_dict.return_value = {"device_name": name, "iot_id": f"iot-{name}"}
    return record


def _entry() -> MagicMock:
    entry = MagicMock()
    entry.runtime_data = _Runtime(
        mowers=[_record(_MOWER, MowingDevice(name=_MOWER), reporting=True)],
        RTK=[_record(_RTK, RTKBaseStationDevice(name=_RTK), reporting=False)],
        spino=[_record(_SPINO, PoolCleanerDevice(name=_SPINO), reporting=False)],
    )
    return entry


async def test_every_device_kind_is_in_the_dump(hass: HomeAssistant) -> None:
    """The reported gap: the Spino was missing from diagnostics."""
    result = await async_get_config_entry_diagnostics(hass, _entry())

    assert set(result) == {_MOWER, _RTK, _SPINO}


async def test_the_pool_cleaner_state_is_serialised(hass: HomeAssistant) -> None:
    """Its own state model has to come through, not just the name."""
    result = await async_get_config_entry_diagnostics(hass, _entry())

    spino = result[_SPINO]
    assert spino["name"] == _SPINO
    # Fields a MowingDevice does not have, so a mower dump could not pass this.
    assert {"pool_state", "pool_map", "bt_mac", "wifi_ssid"} <= set(spino)


async def test_each_device_carries_its_account_record(hass: HomeAssistant) -> None:
    """``device`` is the Aliyun binding, and it is per device, not shared."""
    result = await async_get_config_entry_diagnostics(hass, _entry())

    for name in (_MOWER, _RTK, _SPINO):
        assert result[name]["device"]["device_name"] == name


async def test_an_entry_with_no_pool_cleaner_is_unaffected(
    hass: HomeAssistant,
) -> None:
    """The common case must not start emitting an empty key."""
    entry = _entry()
    entry.runtime_data.spino = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert set(result) == {_MOWER, _RTK}


async def test_network_identifiers_are_redacted(hass: HomeAssistant) -> None:
    """A dump goes into a public issue; the household's network must not."""
    spino = PoolCleanerDevice(name=_SPINO)
    spino.bt_mac = "34:b7:da:6f:7f:ee"
    spino.wifi_mac = "34:b7:da:6f:7f:ec"
    spino.wifi_ssid = "IOT"
    spino.ip = "192.168.20.191"
    entry = _entry()
    entry.runtime_data.spino = [_record(_SPINO, spino, reporting=False)]

    result = await async_get_config_entry_diagnostics(hass, entry)

    dumped = result[_SPINO]
    for key in ("bt_mac", "wifi_mac", "wifi_ssid", "ip"):
        assert dumped[key] == REDACTED, key
    assert "192.168.20.191" not in str(result)
    assert "34:b7:da:6f:7f:ee" not in str(result)


async def test_what_makes_a_dump_readable_survives(hass: HomeAssistant) -> None:
    """Redaction has to stop short of the fields a maintainer reads it for.

    iot_id and the account identity tie a dump's devices together, and lat/lon
    are the whole subject of coordinate bugs like PyMammotion #188.
    """
    rtk = RTKBaseStationDevice(name=_RTK)
    rtk.lat, rtk.lon = -0.674905, 3.059871
    entry = _entry()
    entry.runtime_data.RTK = [_record(_RTK, rtk, reporting=False)]

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result[_RTK]["lat"] == -0.674905
    assert result[_RTK]["lon"] == 3.059871
    assert result[_RTK]["device"]["iot_id"] == f"iot-{_RTK}"
