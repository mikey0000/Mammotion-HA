"""Every mower the account lists is set up, whatever name prefix it is sold under.

``_build_device_list`` decided "is this a mower" by ``DEVICE_SUPPORT = ("Luba",
"Yuka")``.  Mammotion sells mowers under other prefixes (``Ezy-``, ``Kumar-``,
``Maston-``, ``Taru-``) that pymammotion already resolves to mower types by
product key, so those records fell through to the RTK check, failed it, and were
dropped without a log line.
"""

import pytest
from device_list_support import account_device, client_with_lists
from pymammotion.utility.device_type import DeviceType

from custom_components.mammotion import _build_device_list


@pytest.mark.regression
@pytest.mark.parametrize(
    ("device_name", "product_key", "device_type"),
    [
        pytest.param("Ezy-VTABC123", "NnbeYtaEUGE", DeviceType.YUKA_MN100, id="mn100"),
        pytest.param("Ezy-LDABC123", "rBGTwYhfhyY", DeviceType.YUKA_MN101, id="mn101"),
        pytest.param("Kumar-MKABC12", "zkRuTK9KsXG", DeviceType.CM900, id="cm900"),
        pytest.param("Maston-RABC12", "", DeviceType.CM901, id="cm901"),
        pytest.param("Taru-MRABC123", "z8Pu2TEeNZU", DeviceType.LUBA_TR, id="luba-tr"),
    ],
)
def test_a_mower_outside_the_luba_and_yuka_prefixes_is_set_up(
    device_name: str, product_key: str, device_type: DeviceType
) -> None:
    """The record was neither a mower, an RTK nor a Spino, so it vanished."""
    assert DeviceType.value_of_str(device_name, product_key) is device_type

    mowers, rtks, spinos = _build_device_list(
        client_with_lists(mammotion=[account_device(device_name, product_key)])
    )

    assert ([d.device_name for d in mowers], rtks, spinos) == ([device_name], [], [])
