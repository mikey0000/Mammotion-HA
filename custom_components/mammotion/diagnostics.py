"""Diagnostics support for Mammotion."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util.json import json_loads

from . import (
    MammotionConfigEntry,
    MammotionMowerData,
    MammotionRTKData,
    MammotionSpinoData,
)

# Diagnostics get pasted into public issues.  These identify the household's
# network rather than the device, and nothing is diagnosable from them.
# Deliberately not redacted: iot_id, identity_id and nick_name, which are what
# make a multi-device dump readable, and lat/lon, without which a coordinate
# bug cannot be diagnosed at all (see PyMammotion #188).
TO_REDACT: list[str] = [
    "ble_mac",
    "bt_mac",
    "gateway",
    "iccid",
    "imei",
    "imsi",
    "ip",
    "ip_address",
    "mask",
    "mnet_ip",
    "ssid",
    "wifi_mac",
    "wifi_sta_mac",
    "wifi_ssid",
]


def _parse_json_strings(value: Any) -> Any:
    """Unpack strings holding a JSON object or list, so the redactor sees their keys.

    ``mqtt_properties`` carries ``networkInfo`` and kin as JSON strings.
    """
    if isinstance(value, dict):
        return {key: _parse_json_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_parse_json_strings(item) for item in value]
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return _parse_json_strings(json_loads(value))
        except ValueError:
            return value
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    mammotion_devices: list[MammotionMowerData] = entry.runtime_data.mowers
    mammotion_rtk_devices: list[MammotionRTKData] = entry.runtime_data.RTK
    mammotion_spino_devices: list[MammotionSpinoData] = entry.runtime_data.spino
    data = {}
    for mower in mammotion_devices:
        data[mower.name] = asdict(mower.reporting_coordinator.data)
        data[mower.name]["device"] = mower.device.to_dict()

    for rtk in mammotion_rtk_devices:
        data[rtk.name] = asdict(rtk.coordinator.data)
        data[rtk.name]["device"] = rtk.device.to_dict()

    for spino in mammotion_spino_devices:
        data[spino.name] = asdict(spino.coordinator.data)
        data[spino.name]["device"] = spino.device.to_dict()

    # data['entry'] = entry.as_dict()

    return async_redact_data(_parse_json_strings(data), TO_REDACT)
