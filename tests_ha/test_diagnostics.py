"""A diagnostics download has to carry every device on the entry.

A pool cleaner was simply absent: ``async_get_config_entry_diagnostics`` walked
``runtime_data.mowers`` and ``runtime_data.RTK`` and never ``.spino``, so the
one kind of device with its own coordinator and its own state model was the one
missing from the dump people attach to bug reports.
"""

import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import (
    MowingDevice,
    PoolCleanerDevice,
    RTKBaseStationDevice,
)
from pymammotion.data.mqtt.properties import ThingPropertiesMessage

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


async def test_the_sim_and_modem_identifiers_are_redacted(hass: HomeAssistant) -> None:
    """A 4G mower's IMEI, IMSI and ICCID identify the modem and the SIM, not the fault."""
    mower = MowingDevice(name=_MOWER)
    mnet = mower.report_data.dev.mnet_info
    mnet.model, mnet.imei, mnet.imsi, mnet.iccid = "EC200A", "863819075685874", "232010867745532", "89430103525300305328"
    mnet.sim, mnet.link_type, mnet.rssi, mnet.operator = "SIM_OK", "MNET_LINK_4G", -67, "24008"
    entry = _entry()
    entry.runtime_data.mowers = [_record(_MOWER, mower, reporting=True)]

    result = await async_get_config_entry_diagnostics(hass, entry)

    dumped = result[_MOWER]["report_data"]["dev"]["mnet_info"]
    for key in ("imei", "imsi", "iccid"):
        assert dumped[key] == REDACTED, key
    assert (dumped["model"], dumped["sim"], dumped["link_type"], dumped["rssi"], dumped["operator"]) == (
        "EC200A", "SIM_OK", "MNET_LINK_4G", -67, "24008"
    )
    for secret in ("863819075685874", "232010867745532", "89430103525300305328"):
        assert secret not in str(result)


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


# The identifying part of ``networkInfo`` from pymammotion's Yuka fixture, plus the
# 4G fields a cellular unit adds.
_NETWORK_INFO = {
    "ssid": "TestNet",
    "ip": "192.168.1.100",
    "wifi_sta_mac": "02:00:00:12:34:56",
    "wifi_rssi": -62,
    "bt_mac": "02:00:00:12:34:57",
    "imei": "863819075685874",
    "imsi": "232010867745532",
    "iccid": "89430103525300305328",
    "mnet_ip": "10.64.12.7",
    "used_net": 1,
}


def _properties(items: dict) -> ThingPropertiesMessage:
    """Build a ``thing.properties`` envelope as pymammotion stores it on the device."""
    params = dict.fromkeys(
        ("_tenant_id", "group_id", "batch_id", "_trace_id", "request_id", "_category_key", "namespace", "tenant_id"),
        "",
    )
    params |= {
        "device_type": "LawnMower",
        "category_key": "LawnMower",
        "check_failed_data": {},
        "group_id_list": [],
        "gmt_create": 0,
        "generate_time": 0,
        "product_key": "a1biqVGvxrE",
        "device_name": _MOWER,
        "iot_id": f"iot-{_MOWER}",
        "jmsx_delivery_count": 1,
        "check_level": 0,
        "qos": 1,
        "thing_type": "DEVICE",
        "tenant_instance_id": "",
        "items": items,
    }
    return ThingPropertiesMessage.from_dict(
        {"method": "thing.properties", "id": "1", "version": "1.0", "params": params}
    )


def _entry_with_properties(items: dict) -> MagicMock:
    mower = MowingDevice(name=_MOWER)
    mower.mqtt_properties = _properties(items)
    entry = _entry()
    entry.runtime_data.mowers = [_record(_MOWER, mower, reporting=True)]
    return entry


@pytest.mark.regression
async def test_network_info_inside_the_properties_envelope_is_redacted(hass: HomeAssistant) -> None:
    """``networkInfo.value`` is a JSON string, so the redactor never saw the keys in it (#921).

    The raw ``thing.properties`` envelope went into the dump as-is, carrying the
    household's SSID, IP and MACs (and a 4G unit's SIM identifiers) in plain text.
    """
    entry = _entry_with_properties({"networkInfo": {"time": 1, "value": json.dumps(_NETWORK_INFO)}})

    result = await async_get_config_entry_diagnostics(hass, entry)

    network = result[_MOWER]["mqtt_properties"]["params"]["items"]["networkInfo"]["value"]
    for key in ("ssid", "ip", "wifi_sta_mac", "bt_mac", "imei", "imsi", "iccid", "mnet_ip"):
        assert network[key] == REDACTED, key
        assert str(_NETWORK_INFO[key]) not in str(result), key
    # What the blob is read for (signal, which link is in use) survives.
    assert (network["wifi_rssi"], network["used_net"]) == (-62, 1)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("18561", id="a-version-number"),
        pytest.param("{not json", id="looks-like-an-object"),
        pytest.param("", id="empty"),
    ],
)
async def test_a_string_that_is_not_a_json_structure_is_left_as_is(hass: HomeAssistant, value: str) -> None:
    """Only a JSON object or list is unpacked; a scalar string stays the string it was."""
    entry = _entry_with_properties({"rtkVersion": {"time": 1, "value": value}})

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result[_MOWER]["mqtt_properties"]["params"]["items"]["rtkVersion"]["value"] == value


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(json.dumps([json.dumps(_NETWORK_INFO)]), id="json-strings-in-a-json-list"),
        pytest.param(json.dumps({"info": json.dumps(_NETWORK_INFO)}), id="json-inside-json"),
    ],
)
async def test_identifiers_in_any_json_structure_string_are_redacted(hass: HomeAssistant, value: str) -> None:
    """A list, or a JSON string nested in one, must not carry an identifier past the redactor."""
    entry = _entry_with_properties({"deviceOtherInfo": {"time": 1, "value": value}})

    result = await async_get_config_entry_diagnostics(hass, entry)

    for key in ("ssid", "ip", "wifi_sta_mac", "bt_mac", "imei", "imsi", "iccid", "mnet_ip"):
        assert str(_NETWORK_INFO[key]) not in str(result), key
    assert REDACTED in str(result[_MOWER]["mqtt_properties"]["params"]["items"]["deviceOtherInfo"])
