"""``available`` must not call a device live off a raw, unfiltered MQTT socket flag.

``is_online()`` (``handle.has_usable_transport``) is pymammotion's own documented
single source of truth for whether a send would reach the device right now — its
``active_transport()`` selection already treats cloud MQTT as unusable once the
cloud has reported the device offline. A separate "is the MQTT socket merely
connected" check duplicates that decision and gets it wrong: the socket to the
broker can sit open long after the cloud has told us the device itself stopped
responding.
"""

from types import SimpleNamespace

from pymammotion.data.model.device import MowingDevice
from user_command_support import make_cloud_report_coordinator

from custom_components.mammotion.entity import MammotionBaseEntity

_DEVICE_NAME = "Luba-VAME9R5S"


def _available(coordinator: object) -> bool:
    entity = SimpleNamespace(coordinator=coordinator)
    entity._coordinator_available = lambda **kwargs: (
        MammotionBaseEntity._coordinator_available(entity, **kwargs)
    )
    return MammotionBaseEntity.available.fget(entity)


def test_a_device_the_cloud_reports_offline_is_unavailable_even_with_an_open_socket() -> (
    None
):
    """The MQTT transport's socket is connected; the cloud has reported the device offline."""
    coordinator = make_cloud_report_coordinator(_DEVICE_NAME, reported_offline=True)

    assert _available(coordinator) is False


def test_a_device_the_cloud_has_not_reported_offline_is_available() -> None:
    """The same cloud-only handle, without the offline flag, is available."""
    coordinator = make_cloud_report_coordinator(_DEVICE_NAME, reported_offline=False)

    assert _available(coordinator) is True


def test_without_any_data_yet_the_device_is_unavailable_regardless_of_transport() -> (
    None
):
    """No report has arrived yet; a live transport alone should not show real data."""
    coordinator = make_cloud_report_coordinator(_DEVICE_NAME, reported_offline=False)
    coordinator.data = None
    coordinator.manager.get_device_by_name.return_value = MowingDevice()

    assert _available(coordinator) is False
