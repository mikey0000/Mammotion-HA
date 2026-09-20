"""Sweep and dump switches, driven by real report frames.

``tests/test_grass_collection_gate.py`` can only confirm the right expressions
appear in the source.  These build the switch from the same description the
integration registers and read its state off a real ``MowingDevice``, so the
bit positions and the availability rule are actually exercised.
"""

from unittest.mock import MagicMock

import pytest
from pymammotion.data.model.device import MowingDevice

from custom_components.mammotion.switch import (
    GRASS_COLLECTION_SWITCH_ENTITIES,
    MammotionSwitchEntity,
)

_SWEEP, _DUMP = GRASS_COLLECTION_SWITCH_ENTITIES


def _device(*, collector: int = 1, sensor_status: int = 0) -> MowingDevice:
    device = MowingDevice()
    device.report_data.dev.collector_status.collector_installation_status = collector
    device.report_data.dev.sensor_status = sensor_status
    return device


def _switch(description: object, device: MowingDevice) -> MammotionSwitchEntity:
    """Build the real entity class; only the coordinator under it is a stand-in."""
    coordinator = MagicMock()
    coordinator.data = device
    coordinator.grass_collector_installed = device.report_data.dev.collector_installed
    coordinator.grass_collection_state = device.report_data.dev.collector_state
    coordinator.grass_dump_state = device.report_data.dev.dump_state
    entity = MammotionSwitchEntity.__new__(MammotionSwitchEntity)
    entity.coordinator = coordinator
    entity.entity_description = description
    return entity


@pytest.mark.parametrize("description", GRASS_COLLECTION_SWITCH_ENTITIES)
def test_both_switches_are_unavailable_without_a_collector(
    description: object,
) -> None:
    """The app hides them outright on collector_installation_status == 0."""
    entity = _switch(description, _device(collector=0))
    assert entity.entity_description.available_fn(entity.coordinator) is False


@pytest.mark.parametrize("description", GRASS_COLLECTION_SWITCH_ENTITIES)
def test_both_switches_are_available_once_one_is_fitted(description: object) -> None:
    """A fitted collector is the only thing the app gates these on."""
    entity = _switch(description, _device(collector=1))
    assert entity.entity_description.available_fn(entity.coordinator) is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, False), (1, True), (2, False), (3, False)],
)
def test_sweep_is_on_only_while_collecting(raw: int, expected: bool) -> None:
    """sensor_status bits 24-26: 1 is collecting, 2 and 3 are a fault."""
    entity = _switch(_SWEEP, _device(sensor_status=raw << 24))
    assert entity.entity_description.is_on_func(entity.coordinator) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, False), (1, True), (2, False), (3, True)],
)
def test_dump_is_on_while_the_bin_is_not_stowed(raw: int, expected: bool) -> None:
    """Bits 27-29: raised (1) and pouring (3) both count as not stowed."""
    entity = _switch(_DUMP, _device(sensor_status=raw << 27))
    assert entity.entity_description.is_on_func(entity.coordinator) is expected


def test_the_two_bit_fields_do_not_bleed_into_each_other() -> None:
    """A pouring bin must not read as sweeping, nor the reverse."""
    device = _device(sensor_status=(1 << 24) | (3 << 27))
    assert _SWEEP.is_on_func(_switch(_SWEEP, device).coordinator) is True
    assert _DUMP.is_on_func(_switch(_DUMP, device).coordinator) is True

    device = _device(sensor_status=1 << 27)
    assert _SWEEP.is_on_func(_switch(_SWEEP, device).coordinator) is False
