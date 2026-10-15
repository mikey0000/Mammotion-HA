"""The boundary ride distance (``ride_boundary_distance``) follows the app's gate.

The app shows the row only for a fixed set of models, with no firmware compare,
and zeroes the value whenever border laps are off.  Its two positions (0.0 and
0.5) are not a known limit, so the entity accepts any tenth from 0 to 1.  A real
BLE-only entry is set up with the number and select platforms, values go through
HA's public ``number.set_value`` service, and the planned route really comes out
of ``generate_route_information``.
"""

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, create_autospec, patch

import pytest
from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_STEP,
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings
from pymammotion.device.handle import DeviceHandle
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.mammotion as integration
from custom_components.mammotion.const import (
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_KEY = "ride_boundary_distance"
_MOWER = "Luba-VA123456"
_MOWER_MAC = "aa:bb:cc:dd:ee:ff"

_SUPPORTED = [
    "Luba-VA123456",
    "Luba-HM123456",
    "Luba-MB123456",
    "Luba-LA123456",
    "Yuka-MV123456",
    "Yuka-ML123456",
]
_UNSUPPORTED = ["Luba-VS123456", "Yuka-123456"]


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
    """Hold back the client, the device bring-up and every platform but number and select."""

    with (
        patch.object(
            integration, "MammotionClient", side_effect=lambda **_kwargs: _client()
        ),
        patch.object(integration, "PLATFORMS", [Platform.NUMBER, Platform.SELECT]),
        patch.object(integration, "_async_bring_up_devices", autospec=True),
    ):
        yield


async def _set_up(hass: HomeAssistant, name: str) -> MockConfigEntry:
    """Set up a BLE-only entry for *name*; nothing here talks to a mower."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=name,
        data={CONF_HAS_CLOUD_ACCOUNT: False, CONF_BLE_DEVICES: {name: _MOWER_MAC}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def entry(hass: HomeAssistant) -> AsyncIterator[MockConfigEntry]:
    """Load an entry for a supported mower and unload it after the test."""
    loaded = await _set_up(hass, _MOWER)
    yield loaded
    assert await hass.config_entries.async_unload(loaded.entry_id)


def _entity_id(hass: HomeAssistant, domain: str, name: str, key: str) -> str | None:
    """Return the entity id registered for *key* on *name*, if any."""
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{name}_{key}")


def _settings(entry: MockConfigEntry) -> OperationSettings:
    """Return the operation settings the next route is built from."""
    return entry.runtime_data.mowers[0].reporting_coordinator.operation_settings


async def _set(hass: HomeAssistant, value: float) -> None:
    """Set a value through HA's ``number.set_value`` service."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {
            ATTR_ENTITY_ID: _entity_id(hass, NUMBER_DOMAIN, _MOWER, _KEY),
            ATTR_VALUE: value,
        },
        blocking=True,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [(name, True) for name in _SUPPORTED] + [(name, False) for name in _UNSUPPORTED],
)
async def test_the_number_exists_only_for_the_apps_models(
    hass: HomeAssistant, name: str, expected: bool
) -> None:
    """Created for exactly the models the app offers the row on."""
    entry = await _set_up(hass, name)

    assert (_entity_id(hass, NUMBER_DOMAIN, name, _KEY) is not None) is expected
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_gate_does_not_consult_firmware(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The app's gate is model-only, so an unread firmware version still gets it."""
    device = entry.runtime_data.mowers[0].reporting_coordinator.data

    assert device.device_firmwares.device_version == ""
    assert _entity_id(hass, NUMBER_DOMAIN, _MOWER, _KEY) is not None


@pytest.mark.usefixtures("entry")
async def test_the_edge_coverage_select_is_gone(hass: HomeAssistant) -> None:
    """The two-position select is replaced by the number, not kept beside it."""
    assert _entity_id(hass, SELECT_DOMAIN, _MOWER, "edge_coverage") is None


@pytest.mark.usefixtures("entry")
async def test_the_number_spans_zero_to_one_in_tenths_with_no_unit(
    hass: HomeAssistant,
) -> None:
    """The APK names no unit and no hard limit, so none is claimed."""
    entity_id = _entity_id(hass, NUMBER_DOMAIN, _MOWER, _KEY)
    state = hass.states.get(entity_id)

    assert (state.attributes[ATTR_MIN], state.attributes[ATTR_MAX]) == (0, 1)
    assert state.attributes[ATTR_STEP] == 0.1
    assert ATTR_UNIT_OF_MEASUREMENT not in state.attributes
    assert ATTR_DEVICE_CLASS not in state.attributes
    assert er.async_get(hass).async_get(entity_id).entity_category is (
        EntityCategory.CONFIG
    )


async def test_the_number_defaults_to_zero(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A fresh entity stores 0.0, the app's inside-the-edge default."""
    state = hass.states.get(_entity_id(hass, NUMBER_DOMAIN, _MOWER, _KEY))

    assert float(state.state) == 0
    assert _settings(entry).ride_boundary_distance == 0.0


@pytest.mark.parametrize("value", [0.0, 0.2, 0.3, 0.7, 1.0])
async def test_setting_a_value_stores_it_unsnapped(
    hass: HomeAssistant, entry: MockConfigEntry, value: float
) -> None:
    """Any tenth is written as given, not snapped to the app's 0.0/0.5."""
    await _set(hass, value)

    assert _settings(entry).ride_boundary_distance == value


async def test_float_noise_from_the_slider_is_rounded_to_a_tenth(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The frontend's step arithmetic can send 0.1 + 0.2; the route must carry 0.3."""
    await _set(hass, 0.1 + 0.2)

    assert _settings(entry).ride_boundary_distance == 0.3


@pytest.mark.parametrize(
    "value",
    [pytest.param(-0.1, id="below-min"), pytest.param(1.5, id="above-max")],
)
async def test_values_outside_zero_to_one_are_refused(
    hass: HomeAssistant, entry: MockConfigEntry, value: float
) -> None:
    """HA's set_value service enforces the entity's range, so nothing outside it is stored."""
    with pytest.raises(ServiceValidationError):
        await _set(hass, value)

    assert _settings(entry).ride_boundary_distance == 0.0


def _route_coordinator(name: str) -> AsyncMock:
    """Build a stand-in carrying real report data, for the unbound coordinator calls."""
    coordinator = AsyncMock()
    coordinator.device_name = name
    coordinator.data = MowingDevice()
    return coordinator


@pytest.mark.parametrize(
    ("name", "mowing_laps", "expected"),
    [
        ("Luba-VA123456", 1, 0.2),
        ("Yuka-MV123456", 2, 0.2),
        ("Luba-VS123456", 1, 0.0),
        ("Yuka-123456", 1, 0.0),
        ("Luba-VA123456", 0, 0.0),
    ],
    ids=["luba-on", "yuka-on", "unsupported-luba", "unsupported-yuka", "no-laps"],
)
def test_route_generation_gates_the_distance(
    name: str, mowing_laps: int, expected: float
) -> None:
    """The send path gates too: operation_settings survive a device swap.

    With border laps off the app zeroes the value, so the route must carry 0.0
    even when the stored setting is non-zero.
    """
    settings = OperationSettings()
    settings.ride_boundary_distance = 0.2
    settings.mowing_laps = mowing_laps

    route = MammotionBaseUpdateCoordinator.generate_route_information(
        _route_coordinator(name), settings
    )

    assert route.ride_boundary_distance == expected


def _running_job_coordinator() -> SimpleNamespace:
    """Bind the real seeding and modify methods to a fake self over a running job."""
    device = MowingDevice()
    device.work.zone_hashs = [123]
    device.work.edge_mode = 1
    device.work.ride_boundary_distance = 0.3
    device.report_data.work.bp_hash = "123"
    settings = OperationSettings()
    coordinator = SimpleNamespace(
        device_name="Luba-VA123456",
        data=device,
        operation_settings=settings,
        _operation_settings=settings,
        async_send_command=AsyncMock(),
    )
    for name in (
        "_seed_operation_settings_from_running_job",
        "async_modify_plan_route",
        "generate_route_information",
    ):
        setattr(
            coordinator,
            name,
            MethodType(getattr(MammotionBaseUpdateCoordinator, name), coordinator),
        )
    return coordinator


def test_seeding_from_the_running_job_keeps_its_distance() -> None:
    """Without the seed a mid-job tweak re-sends 0.0 and moves the mower inside."""
    coordinator = _running_job_coordinator()

    coordinator._seed_operation_settings_from_running_job()

    assert coordinator._operation_settings.ride_boundary_distance == 0.3


async def test_modifying_the_running_route_resends_its_distance() -> None:
    """``async_modify_plan_route`` re-seeds from ``work`` and must carry the distance."""
    coordinator = _running_job_coordinator()

    await coordinator.async_modify_plan_route(OperationSettings())

    route = coordinator.async_send_command.await_args.kwargs[
        "generate_route_information"
    ]
    assert route.ride_boundary_distance == 0.3


def test_every_translation_names_the_number_and_drops_the_select() -> None:
    """Every locale and icons.json know the number, each in its own language."""
    files = [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]
    assert len(files) > 1
    names: dict[str, str] = {}
    for path in files:
        entity = json.loads(path.read_text())["entity"]
        assert "edge_coverage" not in entity["select"], path
        names[path.name] = entity["number"][_KEY]["name"]
        assert names[path.name], path
    assert names["strings.json"] == "Edge coverage"
    # Only strings.json and en.json may share the English wording.
    english = names["strings.json"]
    assert [f for f, n in names.items() if n == english] == ["strings.json", "en.json"]
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    assert "edge_coverage" not in icons["select"]
    assert icons["number"][_KEY]["default"].startswith("mdi:border")
