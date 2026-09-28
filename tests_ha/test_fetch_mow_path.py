"""Fetching mow path data — the ``fetch_mow_path`` service and the map card's progress trigger.

The map card polls ``get_mow_progress_geojson``.  During a job, while the
progress layer is still empty, that poll asks pymammotion (at most once a
minute) for what the layer is drawn from: the dynamics line on dynamics-line
mowers over the cloud (BLE is left to the library's own loop), otherwise the
cover path.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from pymammotion.utility.constant import WorkMode
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import DOMAIN, LOGGER
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.services import (
    SERVICE_FETCH_MOW_PATH,
    SERVICE_GET_MOW_PROGRESS_GEOJSON,
    async_setup_services,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_MOWER = "Luba-VS1000001"
#: A dynamics-line model (LUBA_LA): its progress layer is the live dynamics line.
_DYNAMICS_MOWER = "Luba-LA1000001"
_PATH_HASH = 7372458040660269014
_PROGRESS = {"type": "FeatureCollection", "features": [{"type": "Feature"}]}


def _coordinator(
    hass: HomeAssistant,
    *,
    device_name: str = _MOWER,
    sys_status: int = WorkMode.MODE_WORKING.value,
    path_hash: int = _PATH_HASH,
    progress: dict[str, Any] | None = None,
    ble_connected: bool = False,
) -> MammotionReportUpdateCoordinator:
    """Build the real coordinator with only what the mow-path methods read."""
    coordinator = MammotionReportUpdateCoordinator.__new__(
        MammotionReportUpdateCoordinator
    )
    coordinator.hass = hass
    coordinator.manager = MagicMock(
        check_and_get_mow_path=AsyncMock(return_value=True),
        check_and_get_dynamics_line=AsyncMock(return_value=True),
    )
    coordinator.manager.mower.return_value.get_transport.return_value = (
        MagicMock(is_connected=True) if ble_connected else None
    )
    coordinator.device_name = device_name
    coordinator.unique_name = device_name
    coordinator.map_offset_lat = 0.0
    coordinator.map_offset_lon = 0.0
    data = MagicMock()
    data.report_data.dev.sys_status = sys_status
    data.report_data.work.path_hash = path_hash
    data.map.generated_mow_progress_geojson = progress or {}
    data.map.generated_dynamics_line_geojson = {}
    data.device_firmwares.device_version = ""
    coordinator.data = data
    coordinator._mow_progress_debouncer = Debouncer(
        hass,
        LOGGER,
        cooldown=60,
        immediate=True,
        function=coordinator._async_refresh_mow_progress_source,
        background=True,
    )
    return coordinator


def _register_mower(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    coordinator: MammotionReportUpdateCoordinator,
) -> str:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        mowers=[MagicMock(reporting_coordinator=coordinator)], spino=[]
    )
    return entity_registry.async_get_or_create(
        "lawn_mower", DOMAIN, coordinator.device_name, config_entry=entry
    ).entity_id


async def _poll_progress(hass: HomeAssistant, entity_id: str) -> Any:
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_MOW_PROGRESS_GEOJSON,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()
    return response


async def test_fetch_mow_path_reports_whether_a_fetch_started(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The service passes pymammotion's answer straight back."""
    async_setup_services(hass)
    coordinator = _coordinator(hass)
    entity_id = _register_mower(hass, entity_registry, coordinator)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_FETCH_MOW_PATH,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )

    assert response == {"fetch_started": True}
    coordinator.manager.check_and_get_mow_path.assert_awaited_once_with(_MOWER)
    coordinator.manager.check_and_get_dynamics_line.assert_not_awaited()


async def test_fetch_mow_path_also_fetches_the_dynamics_line(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The APK fetches both for a dynamics-line mower; either starting counts."""
    async_setup_services(hass)
    coordinator = _coordinator(hass, device_name=_DYNAMICS_MOWER)
    coordinator.manager.check_and_get_mow_path.return_value = False
    entity_id = _register_mower(hass, entity_registry, coordinator)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_FETCH_MOW_PATH,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )

    assert response == {"fetch_started": True}
    coordinator.manager.check_and_get_mow_path.assert_awaited_once_with(_DYNAMICS_MOWER)
    coordinator.manager.check_and_get_dynamics_line.assert_awaited_once_with(
        _DYNAMICS_MOWER
    )


async def test_progress_poll_fetches_a_missing_dynamics_line_over_the_cloud(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Nothing shown yet on a cloud-only mower: fetch the line, not the cover path."""
    async_setup_services(hass)
    coordinator = _coordinator(hass, device_name=_DYNAMICS_MOWER)
    entity_id = _register_mower(hass, entity_registry, coordinator)

    await _poll_progress(hass, entity_id)

    coordinator.manager.check_and_get_dynamics_line.assert_awaited_once_with(
        _DYNAMICS_MOWER
    )
    coordinator.manager.check_and_get_mow_path.assert_not_awaited()
    coordinator._mow_progress_debouncer.async_shutdown()


@pytest.mark.parametrize(
    ("ble_connected", "shown"),
    [
        pytest.param(False, True, id="cloud, line shown"),
        pytest.param(True, False, id="ble, line missing"),
    ],
)
async def test_progress_poll_leaves_the_dynamics_line_alone(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    ble_connected: bool,
    shown: bool,
) -> None:
    """Each cloud fetch costs several invokes; over BLE the library's 10 s loop owns it."""
    async_setup_services(hass)
    coordinator = _coordinator(
        hass, device_name=_DYNAMICS_MOWER, ble_connected=ble_connected
    )
    if shown:
        coordinator.data.map.generated_dynamics_line_geojson = _PROGRESS
    entity_id = _register_mower(hass, entity_registry, coordinator)

    await _poll_progress(hass, entity_id)

    coordinator.manager.check_and_get_dynamics_line.assert_not_awaited()
    coordinator.manager.check_and_get_mow_path.assert_not_awaited()


async def test_empty_progress_during_a_job_fetches_the_path_once_per_cooldown(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The card polls every few seconds; only the first poll in a minute may trigger a fetch."""
    async_setup_services(hass)
    coordinator = _coordinator(hass, sys_status=WorkMode.MODE_PAUSE.value)
    entity_id = _register_mower(hass, entity_registry, coordinator)

    await _poll_progress(hass, entity_id)
    await _poll_progress(hass, entity_id)

    coordinator.manager.check_and_get_mow_path.assert_awaited_once_with(_MOWER)
    coordinator._mow_progress_debouncer.async_shutdown()


@pytest.mark.parametrize(
    ("sys_status", "path_hash", "progress"),
    [
        pytest.param(WorkMode.MODE_READY.value, _PATH_HASH, None, id="no job"),
        pytest.param(WorkMode.MODE_CHARGING.value, _PATH_HASH, None, id="charging"),
        pytest.param(WorkMode.MODE_WORKING.value, 1, None, id="no route"),
        pytest.param(
            WorkMode.MODE_WORKING.value, _PATH_HASH, _PROGRESS, id="progress shown"
        ),
    ],
)
async def test_progress_poll_leaves_the_path_alone(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    sys_status: int,
    path_hash: int,
    progress: dict[str, Any] | None,
) -> None:
    """No fetch outside a job, without a route, or when progress is already shown."""
    async_setup_services(hass)
    coordinator = _coordinator(
        hass, sys_status=sys_status, path_hash=path_hash, progress=progress
    )
    entity_id = _register_mower(hass, entity_registry, coordinator)

    await _poll_progress(hass, entity_id)

    coordinator.manager.check_and_get_mow_path.assert_not_awaited()


async def test_progress_accepts_a_ui_target(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The UI sends targets as a list; the map card sends a plain string."""
    async_setup_services(hass)
    coordinator = _coordinator(hass, progress=_PROGRESS)
    entity_id = _register_mower(hass, entity_registry, coordinator)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_MOW_PROGRESS_GEOJSON,
        {"entity_id": [entity_id]},
        blocking=True,
        return_response=True,
    )

    assert response == _PROGRESS


async def test_an_unknown_mower_raises_a_translated_error(
    hass: HomeAssistant,
) -> None:
    """The map card logs the error rather than silently getting an empty map."""
    async_setup_services(hass)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_MOW_PROGRESS_GEOJSON,
            {"entity_id": "lawn_mower.nope"},
            blocking=True,
            return_response=True,
        )

    assert err.value.translation_key == "mower_not_found"


def test_services_yaml_targets_a_lawn_mower() -> None:
    """The service is declared and targets a Mammotion lawn mower."""
    services = yaml.safe_load((_ROOT / "services.yaml").read_text())
    assert (
        services[SERVICE_FETCH_MOW_PATH]["target"]["entity"]["domain"] == "lawn_mower"
    )


@pytest.mark.parametrize(
    "path",
    [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))],
    ids=lambda path: path.name,
)
def test_every_locale_translates_the_service(path: Path) -> None:
    """Every locale names and describes the service."""
    entry = json.loads(path.read_text(encoding="utf-8"))["services"][
        SERVICE_FETCH_MOW_PATH
    ]
    assert entry["name"]
    assert entry["description"]


@pytest.mark.parametrize(
    ("device_version", "expected"),
    [("1.15.3.4421", False), ("1.15.3.4422", True)],
)
def test_luba_va_dynamics_line_follows_the_device_version(
    hass: HomeAssistant, device_version: str, expected: bool
) -> None:
    """The APK compares the whole-device version; main_controller is a module's own numbering."""
    coordinator = _coordinator(hass, device_name="Luba-VA1000001")
    coordinator.data.device_firmwares.device_version = device_version
    # A real main controller version, which would clear the 1.15.3.4422 bar by itself.
    coordinator.data.device_firmwares.main_controller = "5.1.2.1600 (856231f35)"

    assert coordinator.supports_dynamics_line is expected
