"""Persist pymammotion's error-code cache between Home Assistant restarts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pymammotion.data.error_codes import bundled_error_codes, refresh_error_codes
from pymammotion.http.http import MammotionHTTP

from .const import DOMAIN

STORE_KEY = f"{DOMAIN}.error_codes"
STORE_VERSION = 1
_DATA_KEY = f"{DOMAIN}_error_codes"


@dataclass
class _Persisted:
    store: Store[dict[str, Any]]
    cache: dict[str, Any] | None = None
    loaded: bool = False


async def async_preload_error_codes(hass: HomeAssistant) -> None:
    """Parse pymammotion's bundled error table off the event loop.

    The library reads the table from a CSV inside its package on first use and
    caches it for the process, so whichever caller touches it first pays the file
    read.  Left alone that caller is the event loop — an error sensor, a
    notification, or installing a fetched table — and Home Assistant reports it as
    a blocking call.  Warming it here keeps every later lookup in memory.
    """
    await hass.async_add_executor_job(bundled_error_codes)


async def async_refresh_error_codes(
    hass: HomeAssistant, http: MammotionHTTP | None
) -> None:
    """Refresh the table in Home Assistant's language and save any new cache."""
    if (persisted := hass.data.get(_DATA_KEY)) is None:
        persisted = hass.data[_DATA_KEY] = _Persisted(
            Store(hass, STORE_VERSION, STORE_KEY)
        )
    if not persisted.loaded:
        persisted.cache = await persisted.store.async_load()
        persisted.loaded = True
    if (
        cache := await refresh_error_codes(http, hass.config.language, persisted.cache)
    ) is not None:
        persisted.cache = cache
        await persisted.store.async_save(cache)
