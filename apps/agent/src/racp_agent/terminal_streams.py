import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from racp_domain.models import ExecutionContext, RACPError
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.models import Connected
from racp_protocol.streams import (
    STREAM_IN_FLIGHT_FRAMES,
    StreamAck,
    StreamData,
    StreamEnd,
    StreamGap,
    StreamOpened,
    StreamSubscribe,
    StreamUnsubscribe,
)

from racp_agent.providers.terminal import TerminalProvider, TerminalSession


@dataclass
class Subscription:
    request: StreamSubscribe
    session: TerminalSession
    cursor: int
    consumed: int
    pending: deque[int] = field(default_factory=deque)
    credit: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    def identity(self) -> dict[str, Any]:
        return {
            name: getattr(self.request, name)
            for name in (
                "device_id",
                "agent_boot_id",
                "connection_epoch",
                "stream_id",
                "handle_id",
            )
        }


class TerminalStreams:
    def __init__(
        self,
        provider: TerminalProvider,
        send: Callable[[Connected], Awaitable[None]],
        profile: str,
        lease: Callable[[], float],
        authorize: Callable[[StreamSubscribe], None] | None = None,
    ) -> None:
        self.provider, self.send, self.profile, self.lease = provider, send, profile, lease
        self.subscriptions: dict[str, Subscription] = {}
        self.authorize = authorize or (lambda request: None)

    async def subscribe(self, request: StreamSubscribe) -> None:
        try:
            self.authorize(request)
            if request.stream_id in self.subscriptions:
                raise RACPError("CONFLICT", "stream ID is already active")
            if len(self.subscriptions) >= 16:
                raise RACPError("RESOURCE_EXHAUSTED", "terminal stream limit reached")
            if time.monotonic() >= self.lease():
                raise RACPError("DEVICE_OFFLINE", "execution lease expired", layer="agent")
            if any(
                evaluate("terminal.read", profile_rules(profile)) == Decision.DENY
                for profile in (self.profile, request.context.execution_profile_id)
            ):
                raise RACPError(
                    "PERMISSION_DENIED", "local policy denies terminal stream", layer="agent"
                )
            context = ExecutionContext(
                request.stream_id,
                request.stream_id,
                "0" * 32,
                request.device_id,
                request.context.principal_id,
                request.agent_boot_id,
                20000,
                request.context.workspace_id,
            )
            session = self.provider.identity({"handle_id": request.handle_id}, context)
            if session.state == "EXPIRED" or time.monotonic() >= session.expires:
                raise RACPError("HANDLE_EXPIRED", "terminal expired", layer="provider")
            cursor = int(request.cursor)
            session.buffer.read(cursor, 4, eof=session.eof)
            subscription = Subscription(request, session, cursor, cursor)
            self.subscriptions[request.stream_id] = subscription
            subscription.task = asyncio.create_task(self.run(subscription))
            await asyncio.sleep(0)
        except RACPError as exc:
            await self.send(
                StreamEnd(
                    **{
                        name: getattr(request, name)
                        for name in (
                            "device_id",
                            "agent_boot_id",
                            "connection_epoch",
                            "stream_id",
                            "handle_id",
                        )
                    },
                    byte_offset=request.cursor,
                    reason="error",
                    error=asdict(exc.error),
                )
            )

    def ack(self, message: StreamAck) -> None:
        subscription = self.subscriptions.get(message.stream_id)
        if subscription is None:
            return  # Late ACK after EOF/unsubscribe has no side effect.
        if message.handle_id != subscription.request.handle_id:
            raise RACPError("PERMISSION_DENIED", "stream ACK scope differs")
        offset = int(message.byte_offset)
        if offset <= subscription.consumed:
            return
        if offset not in subscription.pending:
            raise RACPError("INVALID_ARGUMENT", "stream ACK must confirm a sent chunk boundary")
        while subscription.pending and subscription.pending[0] <= offset:
            subscription.pending.popleft()
        subscription.consumed = offset
        subscription.credit.set()

    async def unsubscribe(self, message: StreamUnsubscribe) -> None:
        subscription = self.subscriptions.get(message.stream_id)
        if subscription is not None:
            if message.handle_id != subscription.request.handle_id:
                raise RACPError("PERMISSION_DENIED", "stream unsubscribe scope differs")
            self.subscriptions.pop(message.stream_id, None)
            assert subscription.task is not None
            subscription.task.cancel()
            await asyncio.gather(subscription.task, return_exceptions=True)

    async def wait(self, subscription: Subscription, *, output: bool) -> None:
        observed_end = subscription.session.buffer.end

        async def output_changed() -> None:
            if subscription.session.buffer.end != observed_end or subscription.session.eof:
                return
            await subscription.session.changed.wait()

        credit = asyncio.create_task(subscription.credit.wait())
        changed = asyncio.create_task(output_changed()) if output else None
        tasks = [credit] if changed is None else [credit, changed]
        try:
            await asyncio.wait(tasks, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, subscription: Subscription) -> None:
        request, session = subscription.request, subscription.session
        identity = subscription.identity()
        try:
            self.authorize(request)
            await self.send(
                StreamOpened(
                    **identity,
                    cursor=request.cursor,
                    max_bytes=min(request.max_bytes, request.window_bytes),
                    window_bytes=request.window_bytes,
                )
            )
            while True:
                self.authorize(request)
                subscription.credit.clear()
                session.changed.clear()
                if time.monotonic() >= self.lease():
                    raise RACPError("DEVICE_OFFLINE", "execution lease expired", layer="agent")
                if time.monotonic() >= session.expires or session.state == "EXPIRED":
                    raise RACPError("HANDLE_EXPIRED", "terminal expired", layer="provider")
                remaining = request.window_bytes - (subscription.cursor - subscription.consumed)
                if len(subscription.pending) >= STREAM_IN_FLIGHT_FRAMES or remaining < 4:
                    await self.wait(subscription, output=False)
                    continue
                try:
                    output = session.buffer.read(
                        subscription.cursor, min(request.max_bytes, remaining), eof=session.eof
                    )
                except RACPError as exc:
                    if exc.error.code == "CURSOR_EXPIRED":
                        await self.send(
                            StreamGap(
                                **identity,
                                byte_offset=str(subscription.cursor),
                                earliest_cursor=exc.error.details["earliest_cursor"],
                                lost_bytes=exc.error.details["lost_bytes"],
                            )
                        )
                    raise
                next_cursor = int(output["next_cursor"])
                if next_cursor > subscription.cursor:
                    offset = subscription.cursor
                    subscription.cursor = next_cursor
                    subscription.pending.append(next_cursor)
                    await self.send(
                        StreamData(
                            **identity,
                            byte_offset=str(offset),
                            next_cursor=str(next_cursor),
                            data=output["data"],
                            invalid_byte_replacements=output["invalid_byte_replacements"],
                            eof=output["eof"],
                            process_exit=session.process_exit,
                        )
                    )
                if output["eof"]:
                    await self.send(
                        StreamEnd(
                            **identity,
                            byte_offset=str(subscription.cursor),
                            reason="eof",
                            process_exit=session.process_exit,
                        )
                    )
                    return
                if next_cursor == subscription.cursor and not output["data"]:
                    await self.wait(subscription, output=True)
        except RACPError as exc:
            await self.send(
                StreamEnd(
                    **identity,
                    byte_offset=str(subscription.cursor),
                    reason="error",
                    error=asdict(exc.error),
                )
            )
        except (ConnectionError, OSError):
            pass
        finally:
            if self.subscriptions.get(request.stream_id) is subscription:
                self.subscriptions.pop(request.stream_id, None)

    async def close(self) -> None:
        tasks = [item.task for item in list(self.subscriptions.values()) if item.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.subscriptions.clear()
