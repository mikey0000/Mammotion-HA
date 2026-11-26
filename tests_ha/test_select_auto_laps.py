"""The perimeter and no-go zone laps selects offer Auto where the model's schema does.

The app lists "Auto" (sent as 100) only when the cloud work-setting schema for
the model carries 100 in that row's range (``hasBoundaryAuto``).  A real
BLE-only entry is set up with the select platform, the schema is stored the way
the version coordinator stores it, values go through HA's ``select.select_option``
service, and the settings the next route is built from are read back.
"""

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import create_autospec, patch

import pytest
from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceHandle
from pymammotion.http.model.product_params import (
    ProductParam,
    ProductParamData,
    WorkSettingCode,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

import custom_components.mammotion as integration
from custom_components.mammotion.config import async_get_store
from custom_components.mammotion.const import (
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator

_MOWER = "Luba-VA123456"
_MOWER_MAC = "aa:bb:cc:dd:ee:ff"
_PRODUCT_KEY = "uY54W5rM8YH"
_INT_MOD = "113"
_PERIMETER_LAPS_ENTITY_ID = "select.garden_luba_va123456_perimeter_mowing_laps"
_LAPS = ["0", "1", "2", "3", "4"]
_LAPS_WITH_AUTO = [*_LAPS, "100"]


def _client() -> Any:
    """Return a specced client that keeps the BLE-only record setup hands it."""
    devices: dict[str, MowingDevice] = {}

    async def _add_ble_only_device(
        *, device_name: str, initial_device: MowingDevice, **_kwargs: Any
    ) -> None:
        devices.setdefault(device_name, initial_device)

    client = create_autospec(MammotionClient, instance=True)
    client.mower.return_value = create_autospec(DeviceHandle, instance=True)
    client.add_ble_only_device.side_effect = _add_ble_only_device
    client.get_device_by_name.side_effect = devices.get
    client.to_cache.return_value = {}
    client.aliyun_device_list = []
    client.mammotion_device_list = []
    return client


@pytest.fixture(autouse=True)
def held_back() -> Iterator[None]:
    """Hold back the client, the device bring-up and every platform but select."""
    with (
        patch.object(integration, "MammotionClient", autospec=True) as client_cls,
        patch.object(integration, "PLATFORMS", [Platform.SELECT]),
        patch.object(integration, "_async_bring_up_devices", autospec=True),
    ):
        client_cls.return_value = _client()
        yield


@pytest.fixture
async def entry(hass: HomeAssistant) -> AsyncIterator[MockConfigEntry]:
    """Load a BLE-only entry for one mower and unload it after the test.

    Restore-cache tests seed the cache before requesting this fixture.
    """
    loaded = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_MOWER,
        data={CONF_HAS_CLOUD_ACCOUNT: False, CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC}},
    )
    loaded.add_to_hass(hass)
    assert await hass.config_entries.async_setup(loaded.entry_id)
    await hass.async_block_till_done()
    yield loaded
    assert await hass.config_entries.async_unload(loaded.entry_id)


def _coordinator(entry: MockConfigEntry) -> MammotionReportUpdateCoordinator:
    return entry.runtime_data.mowers[0].reporting_coordinator


def _entity_id(hass: HomeAssistant, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        SELECT_DOMAIN, DOMAIN, f"{_MOWER}_{key}"
    )
    assert entity_id is not None
    return entity_id


async def _store_schema(
    hass: HomeAssistant, entry: MockConfigEntry, auto_for: WorkSettingCode | None
) -> None:
    """Store the model's schema with Auto in the *auto_for* row only, then let the entities update.

    The product key comes from the cloud device list and the internal model from
    the mower's first report; a BLE-only record has neither until then.
    """
    coordinator = _coordinator(entry)
    coordinator.device.product_key = _PRODUCT_KEY
    coordinator.data.mower_state.internal_model = _INT_MOD
    schema = ProductParamData(
        detail_vos=[
            ProductParam(
                code=code, range=_LAPS_WITH_AUTO if code is auto_for else _LAPS
            )
            for code in (WorkSettingCode.edge_mode, WorkSettingCode.obstacle_edge_mode)
        ]
    )
    await async_get_store(hass, entry).async_set_model_capabilities(
        _PRODUCT_KEY, _INT_MOD, schema.to_dict()
    )
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()


def _options(hass: HomeAssistant, key: str) -> list[str]:
    state = hass.states.get(_entity_id(hass, key))
    assert state is not None
    return state.attributes[ATTR_OPTIONS]


@pytest.mark.usefixtures("entry")
@pytest.mark.parametrize("key", ["mowing_laps", "obstacle_laps"])
async def test_auto_is_not_listed_before_the_schema_is_known(
    hass: HomeAssistant, key: str
) -> None:
    """With nothing fetched for the model there is no row to offer Auto from."""
    options = _options(hass, key)

    assert "auto" not in options
    assert options[0] == "none"


@pytest.mark.parametrize(
    ("auto_for", "listed_on", "not_listed_on"),
    [
        pytest.param(
            WorkSettingCode.edge_mode, "mowing_laps", "obstacle_laps", id="perimeter"
        ),
        pytest.param(
            WorkSettingCode.obstacle_edge_mode,
            "obstacle_laps",
            "mowing_laps",
            id="no-go-zones",
        ),
    ],
)
async def test_auto_is_listed_on_the_select_whose_row_offers_it(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    auto_for: WorkSettingCode,
    listed_on: str,
    not_listed_on: str,
) -> None:
    """Each select reads its own schema row, and follows it without a reload."""
    await _store_schema(hass, entry, auto_for)

    assert "auto" in _options(hass, listed_on)
    assert "auto" not in _options(hass, not_listed_on)


async def test_a_schema_without_auto_keeps_it_hidden(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A model whose rows stop at a lap count never shows Auto."""
    await _store_schema(hass, entry, None)

    assert "auto" not in _options(hass, "mowing_laps")
    assert "auto" not in _options(hass, "obstacle_laps")


@pytest.mark.parametrize(
    ("key", "code", "setting"),
    [
        pytest.param(
            "mowing_laps", WorkSettingCode.edge_mode, "mowing_laps", id="perimeter"
        ),
        pytest.param(
            "obstacle_laps",
            WorkSettingCode.obstacle_edge_mode,
            "obstacle_laps",
            id="no-go-zones",
        ),
    ],
)
async def test_choosing_auto_stores_the_apps_sentinel(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    key: str,
    code: WorkSettingCode,
    setting: str,
) -> None:
    """Auto is held as 100, the value the next route sends."""
    await _store_schema(hass, entry, code)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: _entity_id(hass, key), ATTR_OPTION: "auto"},
        blocking=True,
    )

    assert getattr(_coordinator(entry).operation_settings, setting) == 100
    state = hass.states.get(_entity_id(hass, key))
    assert state is not None
    assert state.state == "auto"


async def test_auto_falls_back_to_none_when_the_model_stops_offering_it(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A refetched schema without Auto must not leave 100 to be sent on the next route."""
    await _store_schema(hass, entry, WorkSettingCode.edge_mode)
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: _PERIMETER_LAPS_ENTITY_ID, ATTR_OPTION: "auto"},
        blocking=True,
    )

    await _store_schema(hass, entry, None)

    assert _coordinator(entry).operation_settings.mowing_laps == 0
    state = hass.states.get(_PERIMETER_LAPS_ENTITY_ID)
    assert state is not None
    assert state.state == "none"


@pytest.fixture
def restored_auto(hass: HomeAssistant) -> None:
    """Remember "auto" on the perimeter laps select from a previous run."""
    mock_restore_cache(hass, [State(_PERIMETER_LAPS_ENTITY_ID, "auto")])


@pytest.mark.usefixtures("restored_auto")
async def test_a_restored_auto_the_model_does_not_offer_is_ignored(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A remembered Auto is not sent to a model whose schema does not list it."""
    assert _entity_id(hass, "mowing_laps") == _PERIMETER_LAPS_ENTITY_ID
    assert _coordinator(entry).operation_settings.mowing_laps == 0
    state = hass.states.get(_PERIMETER_LAPS_ENTITY_ID)
    assert state is not None
    assert state.state == "none"
