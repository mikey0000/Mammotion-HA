"""Persisting pymammotion's error-code cache; the refresh logic itself is tested in pymammotion."""

from typing import Any
from unittest.mock import create_autospec, patch

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.data.error_codes import describe, set_fetched_error_codes
from pymammotion.http.http import MammotionHTTP
from pymammotion.http.model.http import ErrorCodeRecord, Response

from custom_components.mammotion.error_codes import (
    _DATA_KEY,
    STORE_KEY,
    async_refresh_error_codes,
)


def _http() -> Any:
    http = create_autospec(MammotionHTTP, instance=True)
    http.get_error_code_version.return_value = Response(code=0, msg="ok", data="v2")
    http.get_all_error_codes_paged.return_value = {
        "1304": ErrorCodeRecord.from_dict(
            {
                "code": "1304",
                "handleList": [{"language": "de", "implication": "Schlechte Ortung"}],
            }
        )
    }
    return http


@pytest.fixture(autouse=True)
def _german(hass: HomeAssistant) -> Any:
    hass.config.language = "de-CH"
    yield
    set_fetched_error_codes(None)


async def test_a_new_cache_is_saved_and_reinstalled_after_a_restart(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """What the library returns is persisted, and a fresh start installs it offline."""
    await async_refresh_error_codes(hass, _http())
    saved = hass_storage[STORE_KEY]["data"]
    assert (saved["version"], saved["language"]) == ("v2", "de")

    set_fetched_error_codes(None)
    hass.data.pop(_DATA_KEY)
    await async_refresh_error_codes(hass, None)

    assert describe(-1304, "de") == "Schlechte Ortung"


async def test_the_store_is_read_once_however_often_coordinators_poll(
    hass: HomeAssistant,
) -> None:
    """Every coordinator update calls the refresh; the file must not be re-read each time."""
    with patch(
        "homeassistant.helpers.storage.Store.async_load", return_value=None
    ) as load:
        await async_refresh_error_codes(hass, None)
        await async_refresh_error_codes(hass, None)

    load.assert_awaited_once()


async def test_nothing_is_saved_when_the_table_is_current(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The library returns no cache when the version and language match."""
    await async_refresh_error_codes(hass, _http())
    set_fetched_error_codes(None)
    hass.data.pop(_DATA_KEY)
    hass_storage[STORE_KEY]["data"]["marker"] = "untouched"
    http = _http()

    await async_refresh_error_codes(hass, http)

    http.get_all_error_codes_paged.assert_not_awaited()
    assert hass_storage[STORE_KEY]["data"]["marker"] == "untouched"
