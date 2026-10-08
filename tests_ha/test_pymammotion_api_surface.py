"""The pymammotion the integration ships against must supply what it imports.

``conftest.py`` stubs most of ``pymammotion`` (and ``bleak``, and the rest) so
the platform modules can be imported without Home Assistant. A module-level
import of a symbol the library does not have therefore sails through the whole
suite and only fails once the integration loads in a real HA — taking every
platform down, not just the feature.

Two separate things are checked here:

* that the library on disk still carries the APIs the integration reaches for —
  a guard against one being removed or renamed upstream;
* that the version Home Assistant will actually install (``manifest.json``) has
  moved past the last release that predates them.

The second is the one that fails today, on purpose. ``pyproject.toml`` points
uv at the sibling checkout for development, so the venv is *ahead* of what
users get: until a release carrying these APIs is cut and the pin bumped, a
HACS install would raise ``ImportError`` / ``AttributeError`` on startup.
"""

import inspect
import json
from importlib.metadata import distribution
from pathlib import Path

import pytest
from pymammotion.client import MammotionClient
from pymammotion.device.handle import DeviceHandle

#: Last release that predates the APIs below.  The pin must move past it.
_RELEASE_WITHOUT_THESE_APIS = "0.9.9"

_MANIFEST = (
    Path(__file__).parent.parent / "custom_components" / "mammotion" / "manifest.json"
)


def _source(relative: str) -> str:
    """Return a source file of the pymammotion the venv resolves, stubs bypassed.

    Handles the editable install ``[tool.uv.sources]`` produces: there the
    dist-info records the source tree in ``direct_url.json`` and nothing lives
    under ``site-packages``.
    """
    dist = distribution("pymammotion")
    if direct_url := dist.read_text("direct_url.json"):
        url = json.loads(direct_url)["url"]
        if url.startswith("file://"):
            root = Path(url.removeprefix("file://")) / "pymammotion"
            if root.is_dir():
                return (root / relative).read_text()
    return (Path(str(dist.locate_file("pymammotion"))) / relative).read_text()


@pytest.mark.parametrize("name", ["CollectorState", "DumpState"])
def test_the_grass_collection_enums_exist(name: str) -> None:
    """switch.py and coordinator.py import these at module level."""
    assert f"class {name}(" in _source("data/model/enums.py")


@pytest.mark.parametrize(
    "accessor", ["collector_state", "dump_state", "collector_installed"]
)
def test_device_data_decodes_the_collector_bits(accessor: str) -> None:
    """The coordinator's grass-collection properties read these off the report frame."""
    assert f"def {accessor}(" in _source("data/model/report_info.py")


def test_the_handle_can_resume_polling() -> None:
    """``restart_keep_alive`` honours a stop, so enabling updates needs this."""
    assert "async def resume_polling(" in _source("device/handle.py")


def test_stop_polling_also_stops_the_ble_loops() -> None:
    """Otherwise a BLE-connected mower keeps streaming with updates switched off."""
    source = _source("device/handle.py")
    stop = source.index("async def stop_polling(")
    body = source[stop : source.index("\n    async def ", stop + 1)]
    assert "_ble_polling_task" in body


@pytest.mark.parametrize("field", ["wifi_mac", "bt_mac"])
def test_the_pool_cleaner_carries_its_macs(field: str) -> None:
    """The Spino device_info builds its ``connections`` from these."""
    source = _source("data/model/device.py")
    start = source.index("class PoolCleanerDevice(")
    body = source[start : source.index("\nclass ", start + 1)]
    assert f'{field}: str = ""' in body


def test_the_pool_cleaner_modes_are_per_model() -> None:
    """select/sensor/vacuum call this to narrow the mode list per device."""
    assert "def for_device(" in _source("data/model/pool_state.py")


def test_add_ble_to_device_accepts_an_rssi() -> None:
    """The advertisement callback has to carry RSSI or is_usable stays latched."""
    source = _source("client.py")
    start = source.index("async def add_ble_to_device(")
    assert "rssi" in source[start : source.index(")", source.index("->", start))]


@pytest.mark.parametrize(
    "method",
    [
        "get_map_backups",
        "get_map_backup_devices",
        "start_map_backup",
        "update_map_backup",
        "restore_map_backup",
        "get_map_backup_progress",
        "cancel_map_backup",
        "cancel_map_restore",
        "delete_map_backup",
    ],
)
def test_the_http_client_wraps_the_map_backup_endpoints(method: str) -> None:
    """The map backup services call these; coordinator.py imports their models."""
    assert f"async def {method}(" in _source("http/http.py")
    assert "class BackupMapItem(" in _source("http/model/map_backup.py")


def test_check_and_get_mow_path_reports_whether_it_fetched() -> None:
    """``fetch_mow_path`` returns this as ``fetch_started``; 0.9.4 returns None.

    The same release fixes the cache check it relies on: the report's
    ``path_hash`` is now compared with the hash of the whole line list.
    """
    source = _source("client.py")
    assert "async def check_and_get_mow_path(self, device_name: str) -> bool:" in source
    assert (
        "async def check_and_get_dynamics_line(self, device_name: str) -> bool:"
        in source
    )
    assert "def is_mow_path_current(" in _source("data/model/hash_list.py")


def test_the_dynamics_line_is_polled_through_a_viewing_window() -> None:
    """``get_mow_progress_geojson`` extends this window on every map-card poll."""
    assert (
        "def watch_dynamics_line(self, device_name: str, account_id: str | None = None) -> None:"
        in _source("client.py")
    )


def test_work_ends_with_the_job_and_fetches_record_their_job() -> None:
    """running_plan and the unknown-job task sync read these from the data."""
    assert "plans_fetched_job_id: int = 0" in _source("data/model/hash_list.py")
    assert "self.work = CurrentTaskSettings()" in _source("data/model/device.py")


def test_the_client_offers_a_user_initiated_status_refresh() -> None:
    """The refresh-status button calls this; 0.9.9 does not have it."""
    refresh_status = getattr(MammotionClient, "refresh_status", None)
    assert inspect.iscoroutinefunction(refresh_status)
    assert list(inspect.signature(refresh_status).parameters) == [
        "self",
        "device_name",
        "account_id",
    ]


def test_the_handle_decides_wifi_movement() -> None:
    """``movement_path`` and the remote-drive entity gate call this; 0.9.9 does not have it."""
    supports = getattr(DeviceHandle, "supports_wifi_movement", None)
    assert callable(supports)
    assert list(inspect.signature(supports).parameters) == ["self"]


@pytest.mark.parametrize(
    "name",
    [
        "RemoteDriveSession",
        "RemoteDrivePhase",
        "RemoteDriveEvent",
        "RemoteDriveEventKind",
        "RemoteDriveError",
    ],
)
def test_the_remote_drive_session_types_exist(name: str) -> None:
    """coordinator.py, sensor.py and notifications.py import these; 0.9.9 does not have them."""
    source = _source("device/remote_drive.py")
    assert f"class {name}(" in source or f"class {name}:" in source


@pytest.mark.parametrize(
    ("method", "parameters"),
    [
        ("start_remote_drive", ["self", "device_name", "account_id", "require_video"]),
        ("confirm_remote_drive", ["self", "device_name", "account_id"]),
        ("remote_drive", ["self", "device_name", "linear", "angular", "account_id"]),
        ("stop_remote_drive", ["self", "device_name", "account_id"]),
        ("subscribe_remote_drive", ["self", "device_name", "handler", "account_id"]),
        ("acknowledge_remote_drive_fence", ["self", "device_name", "account_id"]),
    ],
)
def test_the_client_runs_the_remote_drive_session(
    method: str, parameters: list[str]
) -> None:
    """The coordinator drives the session through these; 0.9.9 does not have them."""
    assert list(inspect.signature(getattr(MammotionClient, method)).parameters) == (
        parameters
    )


def test_the_handle_exposes_its_remote_drive_session() -> None:
    """The coordinator reads the phase off this without creating a session."""
    assert isinstance(DeviceHandle.remote_drive, property)


def test_the_shipped_pin_has_moved_past_the_release_without_these_apis() -> None:
    """What HACS installs — which the local source override hides in development."""
    requirements = json.loads(_MANIFEST.read_text())["requirements"]
    pins = [r for r in requirements if r.startswith("pymammotion")]
    assert pins != [f"pymammotion=={_RELEASE_WITHOUT_THESE_APIS}"], (
        f"manifest.json still pins {_RELEASE_WITHOUT_THESE_APIS}, which predates "
        "the APIs above — cut a release and bump the pin before shipping"
    )


def test_the_positioning_gates_and_accessors_exist() -> None:
    """sensor.py and entity.py gate and read the positioning sensors through these; 0.9.9 has none."""
    device_type = _source("utility/device_type.py")
    assert "def supports_vision_positioning(" in device_type
    assert "def supports_lidar_positioning(" in device_type
    report_info = _source("data/model/report_info.py")
    for accessor in (
        "fuse_localization_status",
        "lidar_positioning_ok",
        "vision_survival",
    ):
        assert f"def {accessor}(" in report_info
    assert "class VioBrightness(" in _source("utility/constant/device_enums.py")
    assert "class FuseLocalizationStatus(UnknownTolerantIntEnum)" in _source(
        "data/model/enums.py"
    )
