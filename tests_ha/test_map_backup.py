"""Cloud map backup and restore services.

The app backs maps up through ``/device-server/v1/map/backup``; pymammotion wraps
those endpoints and the coordinator adds the two things the app does around them:
resolving the backup ``deviceId`` from the cloud's own device list, and treating a
successful envelope whose payload refuses (``result`` false) as a failure.  The
services are exercised on a real Home Assistant; only the HTTP client is a mock.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import entity_registry as er
from pymammotion.http.model.http import Response
from pymammotion.http.model.map_backup import (
    BackupMapItem,
    BackupMapProgress,
    BackupMapResult,
    BackupProgressType,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import (
    MAP_BACKUP_CORRECTION_VALUE,
    MammotionReportUpdateCoordinator,
)
from custom_components.mammotion.services import (
    SERVICE_BACKUP_MAP,
    SERVICE_CANCEL_MAP_BACKUP,
    SERVICE_DELETE_MAP_BACKUP,
    SERVICE_GET_MAP_BACKUP_PROGRESS,
    SERVICE_GET_MAP_BACKUPS,
    SERVICE_RESTORE_MAP,
    async_setup_services,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"
_MOWER = "Luba-VS1000001"
_BACKUP_DEVICE_ID = "backup-dev-1"
_BIZ_ID = "1987654321"
_SERVICES = [
    SERVICE_BACKUP_MAP,
    SERVICE_RESTORE_MAP,
    SERVICE_GET_MAP_BACKUPS,
    SERVICE_GET_MAP_BACKUP_PROGRESS,
    SERVICE_CANCEL_MAP_BACKUP,
    SERVICE_DELETE_MAP_BACKUP,
]
_EXCEPTIONS = [
    "map_backup_cloud_unavailable",
    "map_backup_failed",
    "map_backup_device_not_found",
    "mower_not_found",
]


def _ok[T](data: T) -> Response[T]:
    return Response(code=0, msg="success", data=data)


def _http() -> MagicMock:
    """Return an HTTP client whose backup device list includes the mower."""
    http = MagicMock()
    http.get_map_backup_devices = AsyncMock(
        return_value=_ok(
            [
                BackupMapItem(device_name="Luba-Other", device_id="other"),
                BackupMapItem(device_name=_MOWER, device_id=_BACKUP_DEVICE_ID),
            ]
        )
    )
    http.start_map_backup = AsyncMock(return_value=_ok(BackupMapItem(biz_id=_BIZ_ID)))
    http.update_map_backup = AsyncMock(return_value=_ok(BackupMapItem(biz_id=_BIZ_ID)))
    http.restore_map_backup = AsyncMock(
        return_value=_ok(BackupMapResult(biz_id=_BIZ_ID))
    )
    http.get_map_backups = AsyncMock(
        return_value=_ok(
            [
                BackupMapItem(
                    biz_id=_BIZ_ID,
                    name="Back garden",
                    device_name=_MOWER,
                    area=812,
                    backup_time=1_758_000_000_000,
                    state=1,
                )
            ]
        )
    )
    http.get_map_backup_progress = AsyncMock(
        return_value=_ok(BackupMapProgress(progress=100, state=1))
    )
    http.cancel_map_backup = AsyncMock(return_value=_ok(True))
    http.cancel_map_restore = AsyncMock(return_value=_ok(True))
    http.delete_map_backup = AsyncMock(return_value=_ok(True))
    return http


def _coordinator(http: MagicMock | None) -> MammotionReportUpdateCoordinator:
    """Build the real coordinator with only what the backup methods read."""
    coordinator = MammotionReportUpdateCoordinator.__new__(
        MammotionReportUpdateCoordinator
    )
    coordinator.manager = MagicMock(mammotion_http=http, reauth_required=None)
    coordinator.device_name = _MOWER
    coordinator.unique_name = _MOWER
    return coordinator


def _register_mower(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    coordinator: MammotionReportUpdateCoordinator,
) -> str:
    """Wire the coordinator into a config entry and return its lawn_mower entity_id."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        mowers=[MagicMock(reporting_coordinator=coordinator)], spino=[]
    )
    return entity_registry.async_get_or_create(
        "lawn_mower", DOMAIN, _MOWER, config_entry=entry
    ).entity_id


async def _call(
    hass: HomeAssistant, service: str, data: dict[str, Any], **kwargs: Any
) -> Any:
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, **kwargs
    )


@pytest.fixture
def http() -> MagicMock:
    """HTTP client stand-in."""
    return _http()


@pytest.fixture
async def entity_id(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, http: MagicMock
) -> str:
    """Register a mower and set up the backup services."""
    async_setup_services(hass)
    return _register_mower(hass, entity_registry, _coordinator(http))


async def test_a_backup_uses_the_cloud_device_id_and_returns_the_job_id(
    hass: HomeAssistant, entity_id: str, http: MagicMock
) -> None:
    """The backup deviceId comes from the cloud's list, matched by device name."""
    response = await _call(
        hass,
        SERVICE_BACKUP_MAP,
        {"entity_id": entity_id, "name": "Back garden"},
        return_response=True,
    )

    assert response == {"backup_id": _BIZ_ID}
    http.start_map_backup.assert_awaited_once_with(
        _BACKUP_DEVICE_ID, "Back garden", MAP_BACKUP_CORRECTION_VALUE
    )
    http.update_map_backup.assert_not_awaited()


async def test_a_backup_id_overwrites_that_backup(
    hass: HomeAssistant, entity_id: str, http: MagicMock
) -> None:
    """The app's 'update' flow: the PUT, not a second backup."""
    await _call(
        hass,
        SERVICE_BACKUP_MAP,
        {"entity_id": entity_id, "name": "Back garden", "backup_id": _BIZ_ID},
    )

    http.update_map_backup.assert_awaited_once_with(
        _BIZ_ID, _BACKUP_DEVICE_ID, "Back garden", MAP_BACKUP_CORRECTION_VALUE
    )
    http.start_map_backup.assert_not_awaited()


async def test_restore_targets_the_selected_mower(
    hass: HomeAssistant, entity_id: str, http: MagicMock
) -> None:
    """A backup from any of the account's mowers restores onto the one targeted."""
    await _call(
        hass, SERVICE_RESTORE_MAP, {"entity_id": entity_id, "backup_id": _BIZ_ID}
    )

    http.restore_map_backup.assert_awaited_once_with(_BACKUP_DEVICE_ID, _BIZ_ID)


async def test_a_backup_reply_is_a_success_whatever_its_contents(
    hass: HomeAssistant, entity_id: str, http: MagicMock
) -> None:
    """A live start reply has no ``result`` field; the app sets it for any payload."""
    http.start_map_backup.return_value = _ok(BackupMapItem(biz_id=_BIZ_ID, state=2))

    response = await _call(
        hass,
        SERVICE_BACKUP_MAP,
        {"entity_id": entity_id, "name": "Map1"},
        return_response=True,
    )

    assert response == {"backup_id": _BIZ_ID}


async def test_a_refusal_raises_with_the_envelope_reason(
    hass: HomeAssistant, entity_id: str, http: MagicMock
) -> None:
    """A refusal is ``data: null`` with the reason in the envelope, e.g. a busy mower."""
    http.restore_map_backup.return_value = Response(code=60215, msg="Robot busy")

    with pytest.raises(HomeAssistantError) as err:
        await _call(
            hass, SERVICE_RESTORE_MAP, {"entity_id": entity_id, "backup_id": _BIZ_ID}
        )

    assert err.value.translation_key == "map_backup_failed"
    assert err.value.translation_placeholders == {"reason": "Robot busy (60215)"}


async def test_a_failed_envelope_raises_with_its_message(
    hass: HomeAssistant, entity_id: str, http: MagicMock
) -> None:
    """An HTTP failure comes back from pymammotion as a data-less Response."""
    http.delete_map_backup.return_value = Response(
        code=500, msg="map backup delete failed"
    )

    with pytest.raises(HomeAssistantError) as err:
        await _call(
            hass,
            SERVICE_DELETE_MAP_BACKUP,
            {"entity_id": entity_id, "backup_id": _BIZ_ID},
        )

    assert err.value.translation_placeholders == {
        "reason": "map backup delete failed (500)"
    }


async def test_a_bare_false_reply_is_a_failure(
    hass: HomeAssistant, entity_id: str, http: MagicMock
) -> None:
    """Cancel and delete answer with a bare boolean."""
    http.cancel_map_backup.return_value = _ok(False)

    with pytest.raises(HomeAssistantError):
        await _call(
            hass,
            SERVICE_CANCEL_MAP_BACKUP,
            {"entity_id": entity_id, "backup_id": _BIZ_ID},
        )


async def test_a_mower_missing_from_the_backup_list_is_reported(
    hass: HomeAssistant, entity_id: str, http: MagicMock
) -> None:
    """Rather than guessing its deviceId."""
    http.get_map_backup_devices.return_value = _ok(
        [BackupMapItem(device_name="Luba-Other", device_id="other")]
    )

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, SERVICE_BACKUP_MAP, {"entity_id": entity_id, "name": "Map1"})

    assert err.value.translation_key == "map_backup_device_not_found"
    http.start_map_backup.assert_not_awaited()


async def test_without_a_cloud_login_the_services_raise(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """A BLE-only entry has no HTTP client."""
    async_setup_services(hass)
    entity_id = _register_mower(hass, entity_registry, _coordinator(None))

    with pytest.raises(HomeAssistantError) as err:
        await _call(
            hass,
            SERVICE_GET_MAP_BACKUPS,
            {"entity_id": entity_id},
            return_response=True,
        )

    assert err.value.translation_key == "map_backup_cloud_unavailable"


async def test_the_backup_list_is_returned(hass: HomeAssistant, entity_id: str) -> None:
    """Backup times arrive as epoch milliseconds."""
    response = await _call(
        hass, SERVICE_GET_MAP_BACKUPS, {"entity_id": entity_id}, return_response=True
    )

    (backup,) = response["backups"]
    assert backup["backup_id"] == _BIZ_ID
    assert backup["name"] == "Back garden"
    assert backup["device_name"] == _MOWER
    assert backup["backup_time"] == "2025-09-16T05:20:00+00:00", "epoch ms as ISO UTC"
    assert (
        hass.services.supports_response(DOMAIN, SERVICE_GET_MAP_BACKUPS)
        is SupportsResponse.ONLY
    )


@pytest.mark.parametrize(
    ("restore", "progress_type"),
    [(False, BackupProgressType.BACKUP), (True, BackupProgressType.RESTORE)],
)
async def test_progress_asks_for_the_right_job(
    hass: HomeAssistant,
    *,
    entity_id: str,
    http: MagicMock,
    restore: bool,
    progress_type: BackupProgressType,
) -> None:
    """Backup and restore jobs are polled with different ``type`` codes."""
    response = await _call(
        hass,
        SERVICE_GET_MAP_BACKUP_PROGRESS,
        {"entity_id": entity_id, "backup_id": _BIZ_ID, "restore": restore},
        return_response=True,
    )

    assert response == {"progress": 100, "state": 1, "finished": True}
    http.get_map_backup_progress.assert_awaited_once_with(_BIZ_ID, progress_type)


async def test_cancelling_a_restore_uses_the_restore_endpoint(
    hass: HomeAssistant, entity_id: str, http: MagicMock
) -> None:
    """The app has separate cancel endpoints for the two directions."""
    await _call(
        hass,
        SERVICE_CANCEL_MAP_BACKUP,
        {"entity_id": entity_id, "backup_id": _BIZ_ID, "restore": True},
    )

    http.cancel_map_restore.assert_awaited_once_with(_BACKUP_DEVICE_ID, _BIZ_ID)
    http.cancel_map_backup.assert_not_awaited()


@pytest.mark.parametrize(
    ("service", "data"),
    [
        (SERVICE_BACKUP_MAP, {"name": "Map1"}),
        (SERVICE_RESTORE_MAP, {"backup_id": _BIZ_ID}),
        (SERVICE_CANCEL_MAP_BACKUP, {"backup_id": _BIZ_ID}),
        (SERVICE_DELETE_MAP_BACKUP, {"backup_id": _BIZ_ID}),
    ],
)
async def test_the_writing_services_need_an_admin(
    hass: HomeAssistant,
    *,
    entity_id: str,
    http: MagicMock,
    hass_read_only_user: MockUser,
    service: str,
    data: dict[str, Any],
) -> None:
    """A restore overwrites the mower's map, so these go through the admin check."""
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            service,
            {"entity_id": entity_id, **data},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )

    http.restore_map_backup.assert_not_awaited()


async def test_a_non_mower_entity_is_rejected(
    hass: HomeAssistant, entity_id: str
) -> None:
    """An entity that no config entry owns cannot be resolved to a coordinator."""
    with pytest.raises(HomeAssistantError) as err:
        await _call(
            hass,
            SERVICE_GET_MAP_BACKUPS,
            {"entity_id": "lawn_mower.nope"},
            return_response=True,
        )

    assert err.value.translation_key == "mower_not_found"


def test_services_yaml_declares_every_backup_service() -> None:
    """Each service targets a Mammotion lawn mower."""
    services = yaml.safe_load((_ROOT / "services.yaml").read_text())
    for service in _SERVICES:
        assert services[service]["target"]["entity"]["domain"] == "lawn_mower"


@pytest.mark.parametrize(
    "path",
    [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))],
    ids=lambda path: path.name,
)
def test_every_locale_translates_the_services_and_errors(path: Path) -> None:
    """Every locale needs the service names, field names and error messages."""
    data = json.loads(path.read_text(encoding="utf-8"))
    yaml_services = yaml.safe_load((_ROOT / "services.yaml").read_text())
    for service in _SERVICES:
        entry = data["services"][service]
        assert entry["name"] and entry["description"]
        for field in yaml_services[service].get("fields") or {}:
            assert entry["fields"][field]["name"], (service, field)
    for key in _EXCEPTIONS:
        assert data["exceptions"][key]["message"]
