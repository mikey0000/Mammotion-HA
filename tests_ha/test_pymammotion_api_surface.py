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

import json
from importlib.metadata import distribution
from pathlib import Path

import pytest

#: Last release that predates the APIs below.  The pin must move past it.
_RELEASE_WITHOUT_THESE_APIS = "0.9.0"

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


def test_the_shipped_pin_has_moved_past_the_release_without_these_apis() -> None:
    """What HACS installs — which the local source override hides in development."""
    requirements = json.loads(_MANIFEST.read_text())["requirements"]
    pins = [r for r in requirements if r.startswith("pymammotion")]
    assert pins != [f"pymammotion=={_RELEASE_WITHOUT_THESE_APIS}"], (
        f"manifest.json still pins {_RELEASE_WITHOUT_THESE_APIS}, which predates "
        "the map backup endpoints — cut a release and bump the pin before shipping"
    )
