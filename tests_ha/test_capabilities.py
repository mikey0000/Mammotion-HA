"""The cloud's per-model work-setting schema, fetched once and persisted.

The cloud describes which settings a model exposes and the bounds of each —
blade height 25-70, speed 0.2-1.0, which bypass strategies it accepts — where
the integration otherwise hard-codes them.  That is a property of the
hardware, so it is fetched once per model, stored under
``<product_key>/<int_mod>`` rather than per device, and read back from the
store thereafter.

The lookup key matters: two mowers of the same model share one copy, which is
the mistake the error-code table made (see [[test_store_firmware_checks]]).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import MowingDevice
from pymammotion.http.model.product_params import ProductParam, ProductParamData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.config import MammotionConfigStore
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import (
    MammotionDeviceVersionUpdateCoordinator,
)

_PRODUCT_KEY = "uY54W5rM8YH"
_INT_MOD = "113"
_FIRMWARE = "2.3.30.39"

# Shaped like the rows the endpoint actually returns.
_BLADE_HEIGHT = ProductParam(
    code="3",
    name="刀盘高度",
    is_show=1,
    ui_type="cutHeighSlider",
    min="25",
    max="70",
    step="1",
)
_RIDE_EDGE = ProductParam(
    code="15", name="骑边距离", is_show=0, ui_type="sliderStepPage", default_value="0"
)


def _schema() -> ProductParamData:
    return ProductParamData(
        detail_vos=[_BLADE_HEIGHT, _RIDE_EDGE],
        product_key=_PRODUCT_KEY,
        int_mod=_INT_MOD,
    )


async def _store(hass: HomeAssistant) -> MammotionConfigStore:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    store = MammotionConfigStore(hass, entry.entry_id)
    await store.async_load_device_data()
    return store


def _coordinator(
    store: MammotionConfigStore,
    *,
    int_mod: str = _INT_MOD,
    firmware: str = _FIRMWARE,
    cloud: bool = True,
    returns: ProductParamData | None = None,
) -> MammotionDeviceVersionUpdateCoordinator:
    coordinator = MammotionDeviceVersionUpdateCoordinator.__new__(
        MammotionDeviceVersionUpdateCoordinator
    )
    device = MowingDevice()
    device.mower_state.internal_model = int_mod
    device.device_firmwares.device_version = firmware
    coordinator.data = device
    coordinator.device_name = "Luba-VAME9R5S"
    coordinator.device = MagicMock(product_key=_PRODUCT_KEY)
    coordinator._store = store
    coordinator.manager = MagicMock()
    coordinator.manager.mammotion_http = MagicMock() if cloud else None
    coordinator.manager.reauth_required = None
    coordinator._cloud_api_call = AsyncMock(return_value=returns)
    coordinator._capability_miss = {}
    coordinator._capability_cache = {}
    return coordinator


async def test_it_fetches_and_persists_a_model_schema(hass: HomeAssistant) -> None:
    """The one HTTP call, and what it leaves behind."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=_schema())

    await coordinator._async_ensure_capabilities(coordinator.data)

    coordinator._cloud_api_call.assert_awaited_once()
    stored = store.model_capabilities(_PRODUCT_KEY, _INT_MOD)
    assert stored is not None
    assert {row["code"] for row in stored["detail_vos"]} == {"3", "15"}


async def test_it_is_keyed_by_model_not_by_device(hass: HomeAssistant) -> None:
    """Two mowers of one model must not each store their own copy."""
    store = await _store(hass)
    first = _coordinator(store, returns=_schema())
    await first._async_ensure_capabilities(first.data)

    second = _coordinator(store, returns=_schema())
    second.device_name = "Luba-OTHER"
    await second._async_ensure_capabilities(second.data)

    second._cloud_api_call.assert_not_awaited()
    assert list(store.capabilities) == [f"{_PRODUCT_KEY}/{_INT_MOD}"]


async def test_it_does_not_ask_twice(hass: HomeAssistant) -> None:
    """It describes the hardware, so one fetch per model is the whole point."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=_schema())

    await coordinator._async_ensure_capabilities(coordinator.data)
    await coordinator._async_ensure_capabilities(coordinator.data)

    coordinator._cloud_api_call.assert_awaited_once()


async def test_a_different_model_gets_its_own_entry(hass: HomeAssistant) -> None:
    """The schema differs per internal model, so the key has to as well."""
    store = await _store(hass)
    await _coordinator(store, returns=_schema())._async_ensure_capabilities(
        MowingDevice()
    )

    first = _coordinator(store, returns=_schema())
    await first._async_ensure_capabilities(first.data)
    other = _coordinator(store, int_mod="61", returns=_schema())
    await other._async_ensure_capabilities(other.data)

    assert set(store.capabilities) == {
        f"{_PRODUCT_KEY}/{_INT_MOD}",
        f"{_PRODUCT_KEY}/61",
    }


@pytest.mark.parametrize(
    ("int_mod", "firmware"),
    [("", _FIRMWARE), (_INT_MOD, "")],
    ids=["no-int-mod", "no-firmware"],
)
async def test_it_waits_until_the_device_has_reported_enough(
    hass: HomeAssistant, int_mod: str, firmware: str
) -> None:
    """The endpoint rejects either of these blank, so there is nothing to ask yet."""
    store = await _store(hass)
    coordinator = _coordinator(
        store, int_mod=int_mod, firmware=firmware, returns=_schema()
    )

    await coordinator._async_ensure_capabilities(coordinator.data)

    coordinator._cloud_api_call.assert_not_awaited()
    assert store.capabilities == {}


async def test_a_ble_only_account_asks_nothing(hass: HomeAssistant) -> None:
    """It is an HTTP lookup; with no cloud login there is nobody to ask."""
    store = await _store(hass)
    coordinator = _coordinator(store, cloud=False, returns=_schema())

    await coordinator._async_ensure_capabilities(coordinator.data)

    coordinator._cloud_api_call.assert_not_awaited()


async def test_an_empty_answer_is_not_stored(hass: HomeAssistant) -> None:
    """Nine of one key's models return no rows; storing that would cache a miss."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=ProductParamData(detail_vos=[]))

    await coordinator._async_ensure_capabilities(coordinator.data)

    assert store.capabilities == {}


async def test_a_failed_call_is_not_stored(hass: HomeAssistant) -> None:
    """A failure has to stay retryable rather than becoming a permanent empty."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=None)

    await coordinator._async_ensure_capabilities(coordinator.data)

    assert store.capabilities == {}


async def test_a_setting_can_be_looked_up_by_its_code(hass: HomeAssistant) -> None:
    """What the rest of the integration will read: bounds straight from the cloud."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=_schema())
    await coordinator._async_ensure_capabilities(coordinator.data)

    blade = coordinator.capability("3")

    assert blade is not None
    assert (blade.min, blade.max, blade.step) == ("25", "70", "1")
    assert blade.shown is True


async def test_a_hidden_setting_reports_itself_hidden(hass: HomeAssistant) -> None:
    """Ride-boundary distance is is_show 0 on every model served today."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=_schema())
    await coordinator._async_ensure_capabilities(coordinator.data)

    ride_edge = coordinator.capability("15")

    assert ride_edge is not None
    assert ride_edge.shown is False


async def test_an_unknown_code_and_an_unfetched_model_read_as_nothing(
    hass: HomeAssistant,
) -> None:
    """A caller must be able to ask before anything has been fetched."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=_schema())

    assert coordinator.capability("3") is None

    await coordinator._async_ensure_capabilities(coordinator.data)
    assert coordinator.capability("999") is None


async def test_the_schema_survives_a_restart(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """Persisted, so the HTTP call happens about once per model ever."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    store = MammotionConfigStore(hass, entry.entry_id)
    await store.async_load_device_data()
    coordinator = _coordinator(store, returns=_schema())
    await coordinator._async_ensure_capabilities(coordinator.data)

    reloaded = MammotionConfigStore(hass, entry.entry_id)
    await reloaded.async_load_device_data()

    stored = reloaded.model_capabilities(_PRODUCT_KEY, _INT_MOD)
    assert stored is not None
    assert {row["code"] for row in stored["detail_vos"]} == {"3", "15"}


async def test_an_upgraded_device_refetches_its_schema(hass: HomeAssistant) -> None:
    """The schema is firmware-dependent, so a stored copy must not outlive an OTA.

    Under a model's minProductVersion the cloud serves nothing at all, so a
    copy fetched before an upgrade is not what the device has now.
    """
    store = await _store(hass)
    before = _coordinator(store, returns=_schema())
    await before._async_ensure_capabilities(before.data)

    after = _coordinator(store, firmware="3.0.0.0", returns=_schema())
    await after._async_ensure_capabilities(after.data)

    after._cloud_api_call.assert_awaited_once()
    stored = store.model_capabilities(_PRODUCT_KEY, _INT_MOD)
    assert stored is not None
    assert stored["fetched_for_version"] == "3.0.0.0"


async def test_a_model_with_no_schema_is_not_asked_every_poll(
    hass: HomeAssistant,
) -> None:
    """Nine of one key's models return nothing; re-asking each poll is waste."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=ProductParamData(detail_vos=[]))

    await coordinator._async_ensure_capabilities(coordinator.data)
    await coordinator._async_ensure_capabilities(coordinator.data)
    await coordinator._async_ensure_capabilities(coordinator.data)

    coordinator._cloud_api_call.assert_awaited_once()


async def test_a_new_firmware_retries_a_model_that_had_none(
    hass: HomeAssistant,
) -> None:
    """The empty answer is remembered per firmware, not for good."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=ProductParamData(detail_vos=[]))
    await coordinator._async_ensure_capabilities(coordinator.data)

    upgraded = _coordinator(store, firmware="3.0.0.0", returns=_schema())
    upgraded._capability_miss = coordinator._capability_miss
    await upgraded._async_ensure_capabilities(upgraded.data)

    upgraded._cloud_api_call.assert_awaited_once()
    assert store.model_capabilities(_PRODUCT_KEY, _INT_MOD) is not None


async def test_a_network_failure_does_not_fail_the_update(hass: HomeAssistant) -> None:
    """It is informational; a blip must not strand every entity as unavailable."""
    store = await _store(hass)
    coordinator = _coordinator(store)
    coordinator._cloud_api_call = AsyncMock(side_effect=TimeoutError("boom"))

    await coordinator._async_ensure_capabilities(coordinator.data)

    assert store.capabilities == {}


async def test_a_failure_stays_retryable(hass: HomeAssistant) -> None:
    """A thrown lookup must not be remembered as "this model has none"."""
    store = await _store(hass)
    coordinator = _coordinator(store)
    coordinator._cloud_api_call = AsyncMock(side_effect=TimeoutError("boom"))
    await coordinator._async_ensure_capabilities(coordinator.data)

    coordinator._cloud_api_call = AsyncMock(return_value=_schema())
    await coordinator._async_ensure_capabilities(coordinator.data)

    assert store.model_capabilities(_PRODUCT_KEY, _INT_MOD) is not None


async def test_a_lookup_before_any_state_is_safe(hass: HomeAssistant) -> None:
    """``data`` is None until the first refresh, and entities may ask early."""
    store = await _store(hass)
    coordinator = _coordinator(store)
    coordinator.data = None

    assert coordinator.capability("3") is None


async def test_the_schema_is_parsed_once_per_model(hass: HomeAssistant) -> None:
    """A lookup runs per entity per update; re-parsing ~30 rows each time is waste."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=_schema())
    await coordinator._async_ensure_capabilities(coordinator.data)
    store.model_capabilities = MagicMock(wraps=store.model_capabilities)

    for _ in range(5):
        assert coordinator.capability("3") is not None

    assert store.model_capabilities.call_count == 1


async def test_a_refetch_replaces_the_parsed_copy(hass: HomeAssistant) -> None:
    """Caching the parse must not outlive the schema it was parsed from."""
    store = await _store(hass)
    coordinator = _coordinator(store, returns=_schema())
    await coordinator._async_ensure_capabilities(coordinator.data)
    assert coordinator.capability("15") is not None

    changed = ProductParamData(detail_vos=[_BLADE_HEIGHT], product_key=_PRODUCT_KEY)
    upgraded = _coordinator(store, firmware="3.0.0.0", returns=changed)
    upgraded._capability_cache = coordinator._capability_cache
    await upgraded._async_ensure_capabilities(upgraded.data)

    assert upgraded.capability("15") is None
    assert upgraded.capability("3") is not None
