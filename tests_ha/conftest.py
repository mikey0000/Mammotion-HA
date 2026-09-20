"""Fixtures for tests that run against a real Home Assistant.

Unlike ``tests/``, nothing here is stubbed: ``pytest-homeassistant-custom-component``
supplies a real ``hass``, the entity and device registries and the rest, so these
assert on behaviour rather than on source text.

Run them with the plugin re-enabled, which ``pyproject.toml`` disables globally
for the stubbed suite::

    uv run pytest -p homeassistant tests_ha
"""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load ``custom_components.mammotion``."""
    return
