"""Diagnostics support for Mammotion."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import MammotionConfigEntry, MammotionMowerData, MammotionRTKData

TO_REDACT: list[str] = []


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    mammotion_devices: list[MammotionMowerData] = entry.runtime_data.mowers
    mammotion_rtk_devices: list[MammotionRTKData] = entry.runtime_data.RTK
    data = {}
    for device in mammotion_devices:
        data[device.name] = asdict(device.reporting_coordinator.data)

    for device in mammotion_rtk_devices:
        data[device.name] = asdict(device.coordinator.data)

    # data['entry'] = entry.as_dict()

    return async_redact_data(data, TO_REDACT)
