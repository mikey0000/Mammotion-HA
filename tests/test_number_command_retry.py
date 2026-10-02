"""A failed working-number command must not suppress the next identical request."""

# ruff: noqa: INP001, SLF001

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def number():
    """Execute the actual setter while replacing the transport and HA writes."""
    source = Path(__file__).parent.parent / "custom_components/mammotion/number.py"
    tree = ast.parse(source.read_text())
    working = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "MammotionWorkingNumberEntity"
    )
    # Retain class defaults and the setter so the test exercises its retry state.
    working.bases = []
    working.body = [
        node
        for node in working.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        or isinstance(node, ast.AsyncFunctionDef)
        and node.name == "async_set_native_value"
    ]
    tree.body = [working]
    namespace = {"asyncio": asyncio}
    exec(compile(tree, str(source), "exec"), namespace)  # noqa: S102
    entity = namespace["MammotionWorkingNumberEntity"]()
    entity._attr_native_value = 0.2
    entity.coordinator = SimpleNamespace(speed=0.2)
    entity.entity_description = SimpleNamespace(
        set_fn=lambda coordinator, value: setattr(coordinator, "speed", value),
        set_async_fn=AsyncMock(),
    )
    entity.async_write_ha_state = Mock()
    return entity


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [TimeoutError("timeout"), asyncio.CancelledError()])
async def test_failed_or_cancelled_command_can_be_retried(number, failure):
    """The same value retries after transport failure or caller cancellation."""
    number.entity_description.set_async_fn.side_effect = [failure, None]
    with pytest.raises(type(failure)):
        await number.async_set_native_value(0.4)
    number.async_write_ha_state.assert_not_called()
    await number.async_set_native_value(0.4)
    assert number.entity_description.set_async_fn.await_count == 2
    number.async_write_ha_state.assert_called_once()
    assert number._attr_native_value == number.coordinator.speed == 0.4
    await number.async_set_native_value(0.4)
    assert number.entity_description.set_async_fn.await_count == 2


@pytest.mark.anyio
async def test_coordinator_echo_does_not_hide_failed_command(number):
    """An echoed optimistic value must not make a failed request look successful."""
    number.entity_description.set_async_fn.side_effect = [TimeoutError(), None]
    with pytest.raises(TimeoutError):
        await number.async_set_native_value(0.4)
    # A getter-backed number can echo operation_settings during a later update.
    number._attr_native_value = number.coordinator.speed
    await number.async_set_native_value(0.4)
    assert number.entity_description.set_async_fn.await_count == 2


@pytest.mark.anyio
async def test_successful_duplicate_is_still_a_noop(number):
    """Only successfully completed requests participate in deduplication."""
    await number.async_set_native_value(0.4)
    await number.async_set_native_value(0.4)
    number.entity_description.set_async_fn.assert_awaited_once_with(
        number.coordinator, 0.4
    )
    number.async_write_ha_state.assert_called_once()


@pytest.mark.anyio
async def test_settings_are_available_before_sending(number):
    """Route-building callbacks need the desired setting before they run."""

    async def send(coordinator, value):
        assert coordinator.speed == value == 0.4

    number.entity_description.set_async_fn.side_effect = send
    await number.async_set_native_value(0.4)
    number.async_write_ha_state.assert_called_once()


@pytest.mark.anyio
async def test_local_only_number_still_deduplicates(number):
    """Planning-only fields do not need a transport callback to complete."""
    number.entity_description.set_async_fn = None
    await number.async_set_native_value(0.4)
    await number.async_set_native_value(0.4)
    number.async_write_ha_state.assert_called_once()


@pytest.mark.anyio
async def test_in_flight_duplicate_is_still_a_noop(number):
    """An identical request does not send again while its first attempt runs."""
    started = asyncio.Event()
    finish = asyncio.Event()

    async def send(coordinator, value):
        started.set()
        await finish.wait()

    number.entity_description.set_async_fn.side_effect = send
    pending = asyncio.create_task(number.async_set_native_value(0.4))
    try:
        await started.wait()
        await number.async_set_native_value(0.4)
        assert number.entity_description.set_async_fn.await_count == 1
    finally:
        finish.set()
        await pending
