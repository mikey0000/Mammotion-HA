"""The boundary ride distance (``ride_boundary_distance``) follows the app's gate.

The app shows the row only for a fixed set of models, with no firmware compare,
and zeroes the value whenever border laps are off.  Its two positions (0.0 and
0.5) are not a known limit, so the entity accepts any tenth from 0 to 1.  The
number platform really runs here, values go through HA's own set-value service
wrapper, and the planned route really comes out of
``generate_route_information``.
"""

import json
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components import number as number_component
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import EntityCategory
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.device_config import OperationSettings

from custom_components.mammotion import number as number_platform
from custom_components.mammotion import select as select_platform
from custom_components.mammotion.coordinator import MammotionBaseUpdateCoordinator

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_KEY = "ride_boundary_distance"

_SUPPORTED = [
    "Luba-VA123456",
    "Luba-HM123456",
    "Luba-MB123456",
    "Luba-LA123456",
    "Yuka-MV123456",
    "Yuka-ML123456",
]
_UNSUPPORTED = ["Luba-VS123456", "Yuka-123456"]


def _mower(name: str, firmware: str = "") -> MagicMock:
    """Wrap a real ``MowingDevice`` in the mower record the setup walks."""
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    mower = MagicMock()
    mower.name = name
    mower.device.device_name = name
    mower.device.product_key = ""
    mower.device.iot_id = "iot-id"
    mower.api.get_device_by_name.return_value = None
    coordinator = mower.reporting_coordinator
    coordinator.data = device
    coordinator.device_name = name
    coordinator.unique_name = name
    coordinator.operation_settings = OperationSettings()
    return mower


async def _entities(platform: Any, mower: MagicMock) -> dict[str, Any]:
    """Run a platform's own setup and return what it created, by key."""
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    entry.runtime_data.spino = []
    add_entities = MagicMock()
    await platform.async_setup_entry(MagicMock(), entry, add_entities)
    return {
        entity.entity_description.key: entity
        for call in add_entities.call_args_list
        for entity in call[0][0]
    }


async def _number(name: str = "Luba-VA123456") -> Any:
    """Build the real number entity; state writes need hass, so they are stubbed."""
    entity = (await _entities(number_platform, _mower(name)))[_KEY]
    entity.async_write_ha_state = MagicMock()
    return entity


async def _set(entity: Any, value: float) -> None:
    """Set a value the way HA's ``number.set_value`` service does."""
    await number_component.async_set_value(
        entity, SimpleNamespace(data={"value": value})
    )


def _route_coordinator(name: str) -> AsyncMock:
    """Build a stand-in carrying real report data, for the unbound coordinator calls."""
    coordinator = AsyncMock()
    coordinator.device_name = name
    coordinator.data = MowingDevice()
    return coordinator


@pytest.mark.parametrize(
    ("name", "expected"),
    [(name, True) for name in _SUPPORTED] + [(name, False) for name in _UNSUPPORTED],
)
async def test_the_number_exists_only_for_the_apps_models(
    name: str, expected: bool
) -> None:
    """Created for exactly the models the app offers the row on."""
    entities = await _entities(number_platform, _mower(name, "1.0.0"))
    assert (_KEY in entities) is expected


async def test_the_gate_does_not_consult_firmware() -> None:
    """The app's gate is model-only, so an unread firmware version still gets it."""
    assert _KEY in await _entities(number_platform, _mower("Luba-VA123456", ""))


async def test_the_edge_coverage_select_is_gone() -> None:
    """The two-position select is replaced by the number, not kept beside it."""
    assert "edge_coverage" not in await _entities(
        select_platform, _mower("Luba-VA123456")
    )


async def test_the_number_spans_zero_to_one_in_tenths_with_no_unit() -> None:
    """The APK names no unit and no hard limit, so none is claimed."""
    entity = await _number()

    assert (entity.native_min_value, entity.native_max_value) == (0, 1)
    assert entity.native_step == 0.1
    assert entity.native_unit_of_measurement is None
    assert entity.device_class is None
    assert entity.entity_category is EntityCategory.CONFIG


async def test_the_number_defaults_to_zero() -> None:
    """A fresh entity stores 0.0, the app's inside-the-edge default."""
    entity = await _number()

    assert entity.native_value == 0
    assert entity.coordinator.operation_settings.ride_boundary_distance == 0.0


@pytest.mark.parametrize("value", [0.0, 0.2, 0.3, 0.7, 1.0])
async def test_setting_a_value_stores_it_unsnapped(value: float) -> None:
    """Any tenth is written as given, not snapped to the app's 0.0/0.5."""
    entity = await _number()

    await _set(entity, value)

    assert entity.coordinator.operation_settings.ride_boundary_distance == value


async def test_float_noise_from_the_slider_is_rounded_to_a_tenth() -> None:
    """The frontend's step arithmetic can send 0.1 + 0.2; the route must carry 0.3."""
    entity = await _number()

    await _set(entity, 0.1 + 0.2)

    assert entity.coordinator.operation_settings.ride_boundary_distance == 0.3


async def test_values_above_one_are_refused() -> None:
    """HA's service wrapper enforces the entity's max, so nothing past 1 is stored."""
    entity = await _number()

    with pytest.raises(ServiceValidationError):
        await _set(entity, 1.5)
    assert entity.coordinator.operation_settings.ride_boundary_distance == 0.0


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
    assert names["strings.json"] == "Boundary ride distance"
    # Only strings.json and en.json may share the English wording.
    english = names["strings.json"]
    assert [f for f, n in names.items() if n == english] == ["strings.json", "en.json"]
    icons = json.loads((_ROOT / "icons.json").read_text())["entity"]
    assert "edge_coverage" not in icons["select"]
    assert icons["number"][_KEY]["default"].startswith("mdi:border")
