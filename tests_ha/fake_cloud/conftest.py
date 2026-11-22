"""Fixtures pointing the real integration at pymammotion's fake Mammotion cloud.

The fake lives in the library's source checkout (``tests/fakeserver``), not in
the package, so this tier runs only when that checkout is on the path:
``PYTHONPATH=<Luba-API> uv run --no-sync python -m pytest tests_ha``.  Without
it, or against a library without the transport-availability API, it skips.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pymammotion.client import MammotionClient

pytest.importorskip(
    "tests.fakeserver.cloud",
    reason="pymammotion's fake cloud is not importable; put its source checkout on PYTHONPATH",
)
if not hasattr(MammotionClient, "retry_transport"):
    pytest.skip(
        "the installed pymammotion predates retry_transport", allow_module_level=True
    )

# Imported only once the skips above have passed.
from pymammotion import client as client_module  # noqa: E402
from pymammotion.aliyun import cloud_gateway  # noqa: E402
from pymammotion.http import http  # noqa: E402
from pymammotion.transport.aliyun_mqtt import (  # noqa: E402
    AliyunMQTTConfig,
    AliyunMQTTTransport,
)
from tests.fakeserver.cloud import FakeMammotionCloud  # noqa: E402

import custom_components.mammotion as mammotion_init  # noqa: E402
from custom_components.mammotion.const import DOMAIN  # noqa: E402

#: Ceiling on stopping the fake cloud; it only fires on a genuine hang.
_STOP_TIMEOUT = 30.0


@pytest.fixture
async def fake_cloud(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, socket_enabled: None
) -> AsyncIterator[FakeMammotionCloud]:
    """Start the fake cloud and point every endpoint pymammotion uses at it.

    The Aliyun MQTT host is derived from the product key and region, so the
    transport's config is redirected instead; the fake broker is plaintext.
    Every Mammotion entry is unloaded, stopping its client, before the cloud stops.
    Sockets are enabled for the servers; connects stay limited to 127.0.0.1.
    """
    cloud = FakeMammotionCloud()
    await cloud.start()
    for module, name in (
        (http, "MAMMOTION_DOMAIN"),
        (http, "MAMMOTION_API_DOMAIN"),
        (cloud_gateway, "ALIYUN_DOMAIN"),
        (cloud_gateway, "ALIYUN_OPENACCOUNT_DOMAIN"),
    ):
        monkeypatch.setattr(module, name, cloud.base_url)

    def _loopback_aliyun_config(**kwargs: Any) -> AliyunMQTTConfig:
        return AliyunMQTTConfig(
            **{**kwargs, "host": "127.0.0.1", "port": cloud.aliyun_broker.port}
        )

    async def _no_tls() -> None:
        return None

    monkeypatch.setattr(client_module, "AliyunMQTTConfig", _loopback_aliyun_config)
    monkeypatch.setattr(AliyunMQTTTransport, "get_ssl_context", staticmethod(_no_tls))
    yield cloud
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await asyncio.wait_for(cloud.stop(), timeout=_STOP_TIMEOUT)


@pytest.fixture(autouse=True)
def hold_back_bring_up() -> Iterator[None]:
    """Skip the per-device bring-up: it waits on mower replies the fake never sends."""

    async def _bring_up(*_args: Any, **_kwargs: Any) -> None:
        return None

    with patch.object(mammotion_init, "_async_bring_up_devices", _bring_up):
        yield
