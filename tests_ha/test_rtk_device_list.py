"""An RTK base station must be set up whichever account list it comes from (#971).

pymammotion builds the Mammotion-direct records with an empty ``category_key``,
so the old ``category_key == "Tracker"`` check dropped every RTK that only that
list delivers.
"""

from unittest.mock import MagicMock

from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.client import MammotionClient
from pymammotion.utility.device_type import DeviceType

from custom_components.mammotion import _build_device_list

_RTK_NAME = "RBSA0TEST01"
_RTK_PRODUCT_KEY = "sePx2YW6xDT"


def _device(name: str, product_key: str, category_key: str = "") -> Device:
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


def _client(
    aliyun: list[Device] | None = None, mammotion: list[Device] | None = None
) -> MagicMock:
    """Return a client stand-in holding only the two device lists."""
    client = MagicMock(spec=MammotionClient)
    client.aliyun_device_list = aliyun or []
    client.mammotion_device_list = mammotion or []
    return client


def test_rtk_without_category_from_the_mammotion_list_is_kept() -> None:
    """The record shim leaves category_key empty; the type table still knows it."""
    rtk = _device(_RTK_NAME, _RTK_PRODUCT_KEY)
    assert DeviceType.is_rtk(rtk.device_name, rtk.product_key)

    mowers, rtks, spinos = _build_device_list(_client(mammotion=[rtk]))

    assert [device.device_name for device in rtks] == [_RTK_NAME]
    assert mowers == []
    assert spinos == []


def test_tracker_category_from_the_aliyun_list_is_still_kept() -> None:
    """The Aliyun list reports the category; that path must not change."""
    tracker = _device(_RTK_NAME, _RTK_PRODUCT_KEY, category_key="Tracker")

    _mowers, rtks, _spinos = _build_device_list(_client(aliyun=[tracker]))

    assert [device.device_name for device in rtks] == [_RTK_NAME]


def test_unknown_device_without_category_is_still_dropped() -> None:
    """Only RTKs are rescued; other unsupported records stay out."""
    other = _device("Unknown-ABC123", "unknownKey00")

    mowers, rtks, spinos = _build_device_list(_client(mammotion=[other]))

    assert (mowers, rtks, spinos) == ([], [], [])
