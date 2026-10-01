"""A real pymammotion remote-drive session behind the shipped report coordinator.

The session, its handle and its phases are pymammotion's own; only its edges are
scripted: the token endpoint answers from a list, the cloud send records payloads,
and time moves only when a test calls ``ManualClock.advance``.  The spec'd client's
remote-drive methods forward to that session exactly as ``MammotionClient`` does.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

from homeassistant.core import HomeAssistant
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceHandle
from pymammotion.device.remote_drive import RemoteDriveSession
from pymammotion.http.model.fpv_control import FpvControl
from pymammotion.http.model.http import Response
from pymammotion.proto import (
    DrvSessionCtrlAck,
    DrvSessionCtrlResult,
    LubaMsg,
    MctlDriver,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from user_command_support import make_cloud_handle, make_coordinator

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.const import DOMAIN
from custom_components.mammotion.coordinator import (
    MammotionDeviceErrorUpdateCoordinator,
    MammotionDeviceVersionUpdateCoordinator,
    MammotionMaintenanceUpdateCoordinator,
    MammotionMapUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)
from custom_components.mammotion.models import MammotionDevices, MammotionMowerData
from custom_components.mammotion.notifications import MowerNotifier

#: A Luba 3 on the firmware that restores movement over Wi-Fi.
LUBA3 = "Luba-VAME9R5S"
SUPPORTING_FIRMWARE = "2.3.31.69"
OLD_FIRMWARE = "2.3.30.39"


@dataclass(eq=False)
class _Timer:
    due: float
    callback: Callable[[], Awaitable[None]]
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


class ManualClock:
    """A ``DriveClock`` whose time moves only in :meth:`advance`."""

    def __init__(self) -> None:
        """Start at time zero with nothing scheduled."""
        self._now = 0.0
        self._timers: list[_Timer] = []

    def now_ms(self) -> int:
        """Epoch milliseconds, moved only by :meth:`advance`."""
        return 1_700_000_000_000 + int(self._now * 1000)

    def call_later(
        self, delay: float, callback: Callable[[], Awaitable[None]]
    ) -> _Timer:
        """Schedule *callback* *delay* seconds from now."""
        timer = _Timer(self._now + delay, callback)
        self._timers.append(timer)
        return timer

    async def advance(self, seconds: float = 0.0) -> None:
        """Run every timer due within *seconds*, in due order."""
        target = self._now + seconds
        while due := sorted(
            (t for t in self._timers if not t.cancelled and t.due <= target),
            key=lambda t: t.due,
        ):
            timer = due[0]
            self._timers.remove(timer)
            self._now = max(self._now, timer.due)
            await timer.callback()
        self._now = target


def make_grant(**data: Any) -> Response[FpvControl]:
    """Return a granted control token; overrides use the wire's camelCase keys."""
    wire: dict[str, Any] = {
        "deviceResult": 0,
        "token": "ctl-1",
        "expireIn": 600,
        "timeoutExit": 10,
    }
    wire.update(data)
    return Response(code=0, msg="success", data=FpvControl.from_dict(wire))


@dataclass
class _Tokens:
    """The token endpoint: each request answers with (or raises) the next scripted item."""

    requests: list[Response[FpvControl] | Exception]

    async def request(self) -> Response[FpvControl]:
        item = self.requests.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def refresh(self, token: str) -> Response[FpvControl]:
        return make_grant(token=token)


@dataclass
class _Sent:
    """The session's cloud send: records every payload."""

    payloads: list[bytes] = field(default_factory=list)

    async def __call__(self, payload: bytes, *, timeout: float) -> None:
        self.payloads.append(payload)

    def frames(self) -> list[tuple[Any, ...]]:
        """Decode each payload: ``("drive", linear, angular)`` or ``("exit",)``."""
        decoded: list[tuple[Any, ...]] = []
        for payload in self.payloads:
            driver = LubaMsg().parse(payload).driver
            if (ctrl := driver.todev_session_ctrl_req) is not None:
                decoded.append(("drive", ctrl.set_linear_speed, ctrl.set_angular_speed))
            elif driver.todev_session_exit_nfty is not None:
                decoded.append(("exit",))
        return decoded


@dataclass
class RemoteDriveRig:
    """The coordinator under test and the real session it drives."""

    coordinator: MammotionReportUpdateCoordinator
    handle: DeviceHandle
    session: RemoteDriveSession
    clock: ManualClock
    sent: _Sent

    async def ack(self, ctrl_seq: int, *, result: int = 0, **fields: Any) -> None:
        """Deliver the mower's ack for *ctrl_seq*, then run what it scheduled for now."""
        ack = DrvSessionCtrlAck(
            ctrl_seq=ctrl_seq, result=DrvSessionCtrlResult(result), **fields
        )
        await self.handle.broker.on_message(
            LubaMsg(driver=MctlDriver(toapp_session_ctrl_ack=ack))
        )
        await self.clock.advance(0)


def _forward_to(manager: Any, session: RemoteDriveSession) -> None:
    """Make the spec'd client's remote-drive methods the one-liners ``MammotionClient`` has."""

    async def start(
        _name: str, account_id: str | None = None, *, require_video: bool = False
    ) -> bool:
        session.require_video = require_video
        return await session.start()

    async def confirm(_name: str, account_id: str | None = None) -> None:
        await session.confirm()

    async def drive(
        _name: str, linear: int, angular: int, account_id: str | None = None
    ) -> None:
        await session.drive(linear, angular)

    async def stop(_name: str, account_id: str | None = None) -> None:
        await session.stop()

    manager.remote_drive_session.side_effect = lambda _name, account_id=None: session
    manager.start_remote_drive.side_effect = start
    manager.confirm_remote_drive.side_effect = confirm
    manager.remote_drive.side_effect = drive
    manager.stop_remote_drive.side_effect = stop
    manager.subscribe_remote_drive.side_effect = (
        lambda _name, handler, account_id=None: session.subscribe(handler)
    )
    manager.acknowledge_remote_drive_fence.side_effect = lambda _name, account_id=None: (
        session.acknowledge_fence_warning()
    )


async def make_remote_drive_rig(
    *,
    tokens: list[Response[FpvControl] | Exception] | None = None,
    firmware: str = SUPPORTING_FIRMWARE,
    cloud_usable: bool = True,
    account_in_use: bool = False,
    hass: HomeAssistant | None = None,
    options: dict[str, Any] | None = None,
) -> RemoteDriveRig:
    """Build a coordinator for a cloud-only Luba 3 whose client drives a real session.

    With *hass* the coordinator is built by its own ``__init__`` (listeners, store and
    all); without it, by ``make_coordinator``.  Call ``async_shutdown`` on the former.
    """
    device = MowingDevice()
    device.device_firmwares.device_version = firmware
    handle = make_cloud_handle(
        LUBA3,
        device,
        reported_offline=False,
        cloud_usable=cloud_usable,
        account_in_use=account_in_use,
    )
    # The cloud-connected edge starts the MQTT poll loop, which this rig has no use for.
    await handle.stop_polling()
    clock = ManualClock()
    sent = _Sent()
    session = handle.ensure_remote_drive(
        tokens=_Tokens(tokens if tokens is not None else [make_grant()]),
        send=sent,
        clock=clock,
    )
    if hass is None:
        coordinator = make_coordinator(
            MammotionReportUpdateCoordinator, device, device_name=LUBA3, handle=handle
        )
        coordinator.hass = SimpleNamespace(is_stopping=False)
        coordinator.unique_name = LUBA3
        coordinator.device = _create_ble_only_device(LUBA3)
        if options:
            coordinator.config_entry = SimpleNamespace(options=options)
    else:
        manager = create_autospec(MammotionClient, instance=True)
        manager.get_device_by_name.return_value = device
        manager.mower.return_value = handle
        manager.to_cache.return_value = {}
        entry = MockConfigEntry(domain=DOMAIN, unique_id=LUBA3, options=options or {})
        entry.add_to_hass(hass)
        coordinator = MammotionReportUpdateCoordinator(
            hass, entry, _create_ble_only_device(LUBA3), manager
        )
    coordinator.remote_drive_nudge_s = 0
    _forward_to(coordinator.manager, session)
    return RemoteDriveRig(coordinator, handle, session, clock, sent)


async def make_active_rig(**kwargs: Any) -> RemoteDriveRig:
    """Build a rig whose session the user has started and confirmed."""
    rig = await make_remote_drive_rig(**kwargs)
    await rig.coordinator.async_start_remote_drive()
    await rig.coordinator.async_confirm_remote_drive()
    return rig


def add_entity_listener(
    coordinator: MammotionReportUpdateCoordinator, listener: Callable[[], None]
) -> None:
    """Register *listener* where entities' update callbacks live.

    ``async_add_listener`` also schedules the refresh timer, which a coordinator built
    without ``__init__`` cannot do, so this writes the listener table directly.
    """
    coordinator._listeners[len(coordinator._listeners) + 1] = (listener, None)


def _stand_in(cls: type, device_name: str) -> Any:
    """Return a spec'd coordinator for the entities a platform builds on it."""
    coordinator = create_autospec(cls, instance=True)
    coordinator.unique_name = device_name
    coordinator.device_name = device_name
    return coordinator


def make_mower_data(
    coordinator: MammotionReportUpdateCoordinator,
) -> MammotionMowerData:
    """Return the entry's record of one mower, around its real report coordinator."""
    name = coordinator.device_name
    return MammotionMowerData(
        name=name,
        unique_name=name,
        api=coordinator.manager,
        maintenance_coordinator=_stand_in(MammotionMaintenanceUpdateCoordinator, name),
        reporting_coordinator=coordinator,
        version_coordinator=_stand_in(MammotionDeviceVersionUpdateCoordinator, name),
        map_coordinator=_stand_in(MammotionMapUpdateCoordinator, name),
        error_coordinator=_stand_in(MammotionDeviceErrorUpdateCoordinator, name),
        notifier=create_autospec(MowerNotifier, instance=True),
        device=coordinator.device,
    )


def make_runtime_data(*mowers: MammotionMowerData) -> MammotionDevices:
    """Return an entry's runtime data holding *mowers* and nothing else."""
    return MammotionDevices(mowers=list(mowers), RTK=[], spino=[])
