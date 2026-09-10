"""Working numbers must keep model limits, displayed values and plans consistent."""

# ruff: noqa: INP001, SLF001

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def number_class():
    """Load the real number classes with only their HA boundaries replaced."""

    class BaseEntity:
        def __init__(self, coordinator, key):
            self.coordinator = coordinator
            self.async_get_last_number_data = AsyncMock(return_value=None)
            self.async_write_ha_state = Mock()

        async def async_added_to_hass(self):
            pass

        def _handle_coordinator_update(self):
            pass

    class RestoreNumber:
        pass

    source = Path(__file__).parent.parent / "custom_components/mammotion/number.py"
    tree = ast.parse(source.read_text())
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"MammotionConfigNumberEntity", "MammotionWorkingNumberEntity"}
    ]
    namespace = {
        "Any": Any,
        "cast": cast,
        "MammotionBaseEntity": BaseEntity,
        "RestoreNumber": RestoreNumber,
        "MammotionBaseUpdateCoordinator": list,
        "MammotionConfigNumberEntityDescription": SimpleNamespace,
        "DeviceLimits": SimpleNamespace,
        "EntityCategory": SimpleNamespace(CONFIG="config"),
        "callback": lambda function: function,
        "DEGREE": "°",
    }
    exec(compile(tree, str(source), "exec"), namespace)  # noqa: S102
    return namespace["MammotionWorkingNumberEntity"]


def make_number(number_class, *, minimum=8, maximum=14, height=None):
    """Use Mini limits with the integration's generic planning defaults."""
    settings = SimpleNamespace(channel_width=20, blade_height=height)
    coordinator = SimpleNamespace(operation_settings=settings)
    key, field = (
        ("path_spacing", "channel_width")
        if height is None
        else ("blade_height", "blade_height")
    )
    description = SimpleNamespace(
        key=key,
        native_min_value=20 if height is None else 25,
        native_max_value=35 if height is None else 70,
        native_step=1,
        native_unit_of_measurement="cm" if height is None else "mm",
        set_fn=lambda coordinator, value: setattr(
            coordinator.operation_settings, field, value
        ),
        get_fn=None
        if height is None
        else lambda coordinator: coordinator.operation_settings.blade_height,
        set_async_fn=AsyncMock(),
    )
    limits = SimpleNamespace(**{key: SimpleNamespace(min=minimum, max=maximum)})
    return number_class(coordinator, description, limits), settings


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"), [(8, 14, 14), (25, 35, 25), (20, 35, 20)]
)
def test_initial_spacing_uses_device_limits(number_class, minimum, maximum, expected):
    """Defaults respect both ends of the model range and update the plan."""
    number, settings = make_number(number_class, minimum=minimum, maximum=maximum)
    assert number._attr_native_value == expected
    assert settings.channel_width == expected
    number.entity_description.set_async_fn.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(("saved", "expected"), [(35, 14), (5, 8), (12, 12)])
async def test_restore_validates_before_updating_plan(number_class, saved, expected):
    """Only a normalized value reaches the planning setter during restore."""
    number, settings = make_number(number_class)
    number.async_get_last_number_data.return_value = SimpleNamespace(native_value=saved)
    setter = Mock(wraps=number.entity_description.set_fn)
    number.entity_description.set_fn = setter
    await number.async_added_to_hass()
    assert number._attr_native_value == expected
    assert settings.channel_width == expected
    setter.assert_called_once_with(number.coordinator, expected)
    number.entity_description.set_async_fn.assert_not_called()


@pytest.mark.parametrize(("height", "expected"), [(0, 20), (65, 65), (70, 65)])
def test_initial_height_survives_coordinator_update(number_class, height, expected):
    """An update cannot undo the normalized initial height."""
    number, settings = make_number(number_class, minimum=20, maximum=65, height=height)
    assert number._attr_native_value == expected
    assert settings.blade_height == expected
    number._handle_coordinator_update()
    assert number._attr_native_value == expected
    number.entity_description.set_async_fn.assert_not_called()


@pytest.mark.anyio
async def test_missing_restore_keeps_initialized_value(number_class):
    """A first installation retains its model-specific initial value."""
    number, settings = make_number(number_class)
    await number.async_added_to_hass()
    assert number._attr_native_value == settings.channel_width == 14


def test_no_device_limits_uses_description(number_class):
    """Missing model metadata retains the generic range and default."""
    template, settings = make_number(number_class)
    number = number_class(template.coordinator, template.entity_description, None)
    assert number.native_min_value == 20
    assert number.native_max_value == 35
    assert number._attr_native_value == settings.channel_width == 20
