"""An RTK base station must be set up whichever account list it comes from (#971).

pymammotion builds the Mammotion-direct records with an empty ``category_key``,
so the old ``category_key == "Tracker"`` check dropped every RTK that only that
list delivers.
"""

from device_list_support import account_device, client_with_lists
from pymammotion.utility.device_type import DeviceType

from custom_components.mammotion import _build_device_list

_RTK_NAME = "RBSA0TEST01"
_RTK_PRODUCT_KEY = "sePx2YW6xDT"


def test_rtk_without_category_from_the_mammotion_list_is_kept() -> None:
    """The record shim leaves category_key empty; the type table still knows it."""
    rtk = account_device(_RTK_NAME, _RTK_PRODUCT_KEY)
    assert DeviceType.is_rtk(rtk.device_name, rtk.product_key)

    mowers, rtks, spinos = _build_device_list(client_with_lists(mammotion=[rtk]))

    assert [device.device_name for device in rtks] == [_RTK_NAME]
    assert mowers == []
    assert spinos == []


def test_tracker_category_from_the_aliyun_list_is_still_kept() -> None:
    """The Aliyun list reports the category; that path must not change."""
    tracker = account_device(_RTK_NAME, _RTK_PRODUCT_KEY, category_key="Tracker")

    _mowers, rtks, _spinos = _build_device_list(client_with_lists(aliyun=[tracker]))

    assert [device.device_name for device in rtks] == [_RTK_NAME]


def test_unknown_device_without_category_is_still_dropped() -> None:
    """Only RTKs are rescued; other unsupported records stay out."""
    other = account_device("Unknown-ABC123", "unknownKey00")

    mowers, rtks, spinos = _build_device_list(client_with_lists(mammotion=[other]))

    assert (mowers, rtks, spinos) == ([], [], [])
