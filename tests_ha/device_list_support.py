"""Builders for the tests that feed account device lists to ``_build_device_list``."""

from unittest.mock import MagicMock

from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.client import MammotionClient


def account_device(name: str, product_key: str, category_key: str = "") -> Device:
    """Return an account device record as the cloud lists hand it over."""
    return Device(
        gmt_modified=0,
        node_type="DEVICE",
        device_name=name,
        product_name=name,
        status=1,
        identity_id=name,
        net_type="WIFI",
        category_key=category_key,
        product_key=product_key,
        is_edge_gateway=False,
        category_name="",
        identity_alias=name,
        iot_id=f"iot-{name}",
        bind_time=0,
        owned=1,
        thing_type="DEVICE",
    )


def client_with_lists(
    aliyun: list[Device] | None = None, mammotion: list[Device] | None = None
) -> MagicMock:
    """Return a client stand-in holding only the two device lists."""
    client = MagicMock(spec=MammotionClient)
    client.aliyun_device_list = aliyun or []
    client.mammotion_device_list = mammotion or []
    return client
