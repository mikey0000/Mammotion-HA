"""Fixtures for the test suite, which runs against a real Home Assistant.

Nothing here is stubbed: ``pytest-homeassistant-custom-component`` supplies a
real ``hass``, the entity and device registries and the rest, so these assert
on behaviour rather than on source text.  This replaced an earlier ``tests/``
that stubbed out parts of ``homeassistant`` so the platform modules could be
imported without one.

``pyproject.toml`` points ``testpaths`` here, so ``uv run pytest`` is all it
takes; the plugin loads by itself.
"""

from collections.abc import AsyncIterator

import pytest
from user_command_support import LIVE_HANDLES


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load ``custom_components.mammotion``."""
    return


@pytest.fixture(autouse=True)
async def stop_cloud_handle_poll_loops() -> AsyncIterator[None]:
    """Stop the poll loop a CONNECTED ``make_cloud_handle`` handle starts."""
    yield
    while LIVE_HANDLES:
        await LIVE_HANDLES.pop().stop_polling()
