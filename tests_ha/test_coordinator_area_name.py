"""``get_area_entity_name`` and the shared error formatter on the coordinators.

The area part covers the "Area N" fallback for areas whose device-assigned name
is an empty string — before it, the work_area and task_area sensors showed an
unreadable ``area 1451834635207421727``.  The stubbed version re-implemented
``HashList.computed_areas`` and faked the registry lookup; here both are real,
so a renamed entity is genuinely renamed and the numbering is the library's.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pymammotion.data.error_codes import set_fetched_error_codes
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.hash_list import AreaHashNameList, FrameList
from pymammotion.http.model.http import ErrorInfo

from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import (
    MammotionDeviceErrorUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)

_DEVICE = "Luba-VS123456"


def _coordinator(
    hass: HomeAssistant,
    area_hashes: list[int],
    area_names: list[tuple[str, int]],
    *,
    ha_names: dict[int, str] | None = None,
) -> MammotionReportUpdateCoordinator:
    """Build a report coordinator with a real map, bypassing the heavy __init__.

    ``ha_names`` maps an area hash to the name a user gave that switch in Home
    Assistant, registered on the real entity registry.
    """
    coordinator = MammotionReportUpdateCoordinator.__new__(
        MammotionReportUpdateCoordinator
    )
    coordinator.data = MowingDevice()
    coordinator.data.map.area = {h: FrameList() for h in area_hashes}
    coordinator.data.map.area_name = [
        AreaHashNameList(name=name, hash=h) for name, h in area_names
    ]
    coordinator.unique_name = _DEVICE
    coordinator.hass = hass

    registry = er.async_get(hass)
    for area_hash, name in (ha_names or {}).items():
        entry = registry.async_get_or_create(
            "switch",
            DOMAIN,
            f"{_DEVICE}_{area_hash}",
            suggested_object_id=f"area_{area_hash}",
        )
        registry.async_update_entity(entry.entity_id, name=name)
    return coordinator


class TestGetAreaEntityName:
    """Unnamed areas must read as "Area N", the same label the map shows."""

    async def test_zero_hash_returns_none(self, hass: HomeAssistant) -> None:
        """Hash == 0 is the sentinel for "no zone"."""
        assert _coordinator(hass, [], []).get_area_entity_name(0) is None

    async def test_hash_not_in_area_returns_path(self, hass: HomeAssistant) -> None:
        """A hash with no area frame belongs to the mow path, not an area."""
        assert _coordinator(hass, [], []).get_area_entity_name(999) == "path"

    async def test_real_name_returned_as_is(self, hass: HomeAssistant) -> None:
        """A device-assigned name is what the user set in the app."""
        h = 111
        coordinator = _coordinator(hass, [h], [("Front Lawn", h)])
        assert coordinator.get_area_entity_name(h) == "Front Lawn"

    async def test_empty_name_returns_area_n(self, hass: HomeAssistant) -> None:
        """The raw hash is unreadable; the fallback must be the "Area N" label."""
        h = 111
        coordinator = _coordinator(hass, [h], [("", h)])
        assert coordinator.get_area_entity_name(h) == "Area 1"

    async def test_empty_name_not_zone_n(self, hass: HomeAssistant) -> None:
        """The prefix was briefly "Zone" during a GeojsonGenerator refactor."""
        h = 111
        result = _coordinator(hass, [h], [("", h)]).get_area_entity_name(h)
        assert result is not None
        assert not result.lower().startswith("zone")

    async def test_two_unnamed_areas_are_numbered_one_and_two(
        self, hass: HomeAssistant
    ) -> None:
        """Both must get a number; neither may be left showing its hash."""
        h1, h2 = 100, 200
        coordinator = _coordinator(hass, [h1, h2], [("", h1), ("", h2)])

        assert coordinator.get_area_entity_name(h1) == "Area 1"
        assert coordinator.get_area_entity_name(h2) == "Area 2"

    async def test_numbering_does_not_follow_hash_magnitude(
        self, hass: HomeAssistant
    ) -> None:
        """Numbers are handed out in map order, so a huge hash can still be 1."""
        h_large, h_small = 9_999_999, 1
        coordinator = _coordinator(
            hass, [h_large, h_small], [("", h_large), ("", h_small)]
        )

        assert coordinator.get_area_entity_name(h_large) == "Area 1"
        assert coordinator.get_area_entity_name(h_small) == "Area 2"

    async def test_a_hash_missing_from_area_name_still_gets_a_label(
        self, hass: HomeAssistant
    ) -> None:
        """computed_areas auto-assigns rather than exposing the raw hash."""
        h = 12345
        assert _coordinator(hass, [h], []).get_area_entity_name(h) == "Area 1"

    async def test_mixed_named_and_unnamed(self, hass: HomeAssistant) -> None:
        """A named area does not consume a number, so the unnamed one is "Area 1"."""
        h_named, h_unnamed = 10, 20
        coordinator = _coordinator(
            hass, [h_named, h_unnamed], [("Back Garden", h_named), ("", h_unnamed)]
        )

        assert coordinator.get_area_entity_name(h_named) == "Back Garden"
        assert coordinator.get_area_entity_name(h_unnamed) == "Area 1"


class TestHomeAssistantNameOverride:
    """A switch the user renamed in HA must carry that name into the sensors.

    Otherwise work_area and task_area keep showing the device's name for an
    area the dashboard calls something else.
    """

    async def test_a_user_set_name_beats_the_device_name(
        self, hass: HomeAssistant
    ) -> None:
        """The HA label is the one the user is looking at."""
        h = 111
        coordinator = _coordinator(
            hass, [h], [("Front Garden", h)], ha_names={h: "My Front Zone"}
        )
        assert coordinator.get_area_entity_name(h) == "My Front Zone"

    async def test_a_user_set_name_beats_the_area_n_fallback(
        self, hass: HomeAssistant
    ) -> None:
        """Naming an otherwise unnamed area is the common reason to rename one."""
        h = 222
        coordinator = _coordinator(hass, [h], [("", h)], ha_names={h: "Side Strip"})
        assert coordinator.get_area_entity_name(h) == "Side Strip"

    async def test_an_unrenamed_entity_falls_back_to_the_computed_name(
        self, hass: HomeAssistant
    ) -> None:
        """A registry row with no user name must not blank out the device name."""
        h = 333
        coordinator = _coordinator(hass, [h], [("Back Lawn", h)], ha_names={})
        assert coordinator.get_area_entity_name(h) == "Back Lawn"

    async def test_a_user_set_name_does_not_bleed_onto_another_hash(
        self, hass: HomeAssistant
    ) -> None:
        """The lookup is keyed by hash, so only that one area is affected."""
        h1, h2 = 10, 20
        coordinator = _coordinator(
            hass, [h1, h2], [("", h1), ("", h2)], ha_names={h1: "My Zone"}
        )

        assert coordinator.get_area_entity_name(h1) == "My Zone"
        assert coordinator.get_area_entity_name(h2) == "Area 2"


def _error_info(**overrides: str) -> ErrorInfo:
    """One row of the device's error table, with only the fields under test set."""
    fields = dict.fromkeys(ErrorInfo.__dataclass_fields__, "")
    return ErrorInfo(**{**fields, **overrides})


def _error_coordinator(
    hass: HomeAssistant,
    *,
    language: str = "de",
    codes: dict[str, ErrorInfo] | None = None,
    cls: type = MammotionReportUpdateCoordinator,
) -> MammotionReportUpdateCoordinator | MammotionDeviceErrorUpdateCoordinator:
    """Build a coordinator over the process-wide error table.

    The table used to hang off each device record; it is installed once now, so
    the helper installs it and ``_clear_error_table`` takes it away again.
    """
    coordinator = cls.__new__(cls)
    coordinator.device_name = "Luba-1"
    coordinator.hass = hass
    hass.config.language = language
    set_fetched_error_codes(codes)
    coordinator.manager = MagicMock()
    coordinator.manager.get_device_by_name.return_value = MagicMock()
    return coordinator


@pytest.fixture(autouse=True)
def _clear_error_table() -> Iterator[None]:
    """Clear the process-wide fetched table so it cannot leak between tests."""
    yield
    set_fetched_error_codes(None)


async def test_report_coordinator_describes_known_code_in_ha_language(
    hass: HomeAssistant,
) -> None:
    """Any coordinator can format an error, in the language HA is set to.

    The error table lives on the shared device record, and an empty localised
    field falls back to English.
    """
    info = _error_info(
        module="nav",
        level="warning",
        en_implication="Lost RTK",
        en_solution="Move it",
        de_implication="RTK verloren",
    )
    coordinator = _error_coordinator(hass, codes={"99801": info})

    assert coordinator.describe_error_code(-99801) == {
        "module": "nav",
        "level": "warning",
        "message": "RTK verloren",
        "solution": "Move it",
        "text": "nav: RTK verloren, Move it",
    }


async def test_describe_error_code_leaves_text_empty_without_an_implication(
    hass: HomeAssistant,
) -> None:
    """A bare "nav: " would read as a description of the fault."""
    info = _error_info(module="nav", level="1", en_solution="Move it")
    coordinator = _error_coordinator(
        hass, codes={"99801": info}, cls=MammotionDeviceErrorUpdateCoordinator
    )

    assert coordinator.describe_error_code(99801)["text"] == ""
    coordinator.data = MagicMock()
    coordinator.data.errors.err_code_list = [99801]
    assert coordinator.get_error_message(0) == "Error message not found"


async def test_get_error_message_uses_the_shared_formatter(
    hass: HomeAssistant,
) -> None:
    """The sensors and the notification event must never word an error differently."""
    info = _error_info(
        module="nav",
        level="1",
        en_implication="Lost RTK",
        en_solution="Move it",
        de_implication="RTK verloren",
        de_solution="Bewegen",
    )
    coordinator = _error_coordinator(
        hass, codes={"99801": info}, cls=MammotionDeviceErrorUpdateCoordinator
    )
    coordinator.data = MagicMock()
    coordinator.data.errors.err_code_list = [-99801]

    assert coordinator.get_error_message(0) == "nav: RTK verloren, Bewegen"
    coordinator.data.errors.err_code_list = []
    assert coordinator.get_error_message(0) == "No Error"


async def test_async_bring_up_runs_setup_once_then_refreshes(
    hass: HomeAssistant,
) -> None:
    """Bring-up must run the one-time setup hook exactly once.

    Home Assistant only runs ``_async_setup`` from the first-refresh path,
    which the background bring-up does not use.
    """
    coordinator = _error_coordinator(hass)
    coordinator._bring_up_done = False  # noqa: SLF001
    coordinator._async_setup = AsyncMock()  # noqa: SLF001
    coordinator.async_refresh = AsyncMock()

    await coordinator.async_bring_up()
    await coordinator.async_bring_up()

    coordinator._async_setup.assert_awaited_once()  # noqa: SLF001
    assert coordinator.async_refresh.await_count == 2


async def test_async_bring_up_marks_failure_and_skips_refresh_when_setup_raises(
    hass: HomeAssistant,
) -> None:
    """Mirrors DataUpdateCoordinator's own setup guard rather than raising."""
    coordinator = _error_coordinator(hass)
    coordinator._bring_up_done = False  # noqa: SLF001
    coordinator._async_setup = AsyncMock(side_effect=RuntimeError("no handle"))  # noqa: SLF001
    coordinator.async_refresh = AsyncMock()

    await coordinator.async_bring_up()

    coordinator.async_refresh.assert_not_awaited()
    assert coordinator.last_update_success is False


async def test_describe_error_code_returns_none_when_it_cannot_look_one_up(
    hass: HomeAssistant,
) -> None:
    """A code neither the fetched table nor the bundle knows yields no text.

    The device record is no longer consulted — the table is process-wide — so a
    device that has gone away no longer affects the answer, which is the point.
    """
    assert _error_coordinator(hass).describe_error_code(999999) is None

    coordinator = _error_coordinator(hass)
    coordinator.manager.get_device_by_name.return_value = None
    assert coordinator.describe_error_code(999999) is None


async def test_describe_error_code_keeps_bundled_text_over_a_blank_fetched_row(
    hass: HomeAssistant,
) -> None:
    """Some accounts' export lists 1304 with no text; the notification read "Code 1304"."""
    blank = _error_info(
        code="1304", module="navigation", level="1", description="定位状态差"
    )
    coordinator = _error_coordinator(hass, language="en", codes={"1304": blank})

    info = coordinator.describe_error_code(-1304)

    assert info is not None
    assert info["message"] == "Poor positioning status"
    assert info["solution"].startswith("The robot has reset the onboard RTK module")


async def test_describe_error_code_reads_a_region_tagged_language(
    hass: HomeAssistant,
) -> None:
    """HA's language is a BCP 47 tag such as ``de-CH``; the table is keyed by ``de``."""
    info = _error_info(en_implication="Lost RTK", de_implication="RTK verloren")
    coordinator = _error_coordinator(hass, language="de-CH", codes={"99801": info})

    assert coordinator.describe_error_code(99801)["message"] == "RTK verloren"


@pytest.mark.parametrize(
    ("reauth_required", "expects_http"),
    [(None, True), ("rejected", False)],
    ids=["live-login", "awaiting-reauth"],
)
async def test_error_code_refresh_offers_the_cloud_only_while_the_login_is_live(
    hass: HomeAssistant, reauth_required: str | None, expects_http: bool
) -> None:
    """A login awaiting re-authentication must not be used; the stored table still installs."""
    coordinator = _error_coordinator(hass)
    coordinator.manager.reauth_required = reauth_required

    with patch(
        "custom_components.mammotion.coordinator.async_refresh_error_codes", AsyncMock()
    ) as refresh:
        await coordinator._async_refresh_error_codes()  # noqa: SLF001

    expected = coordinator.manager.mammotion_http if expects_http else None
    refresh.assert_awaited_once_with(hass, expected)
