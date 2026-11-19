"""Bridge timer delivery to existing session events, without a new engine hook."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from intern.events import Event, MessageEvent, RunEnd, RunStart
from intern.messages import UserMessage
from intern.session import Session


@dataclass
class _Delivery:
    text: str
    ended: asyncio.Future[None]
    run_id: str | None = None


class TimerDelivery:
    def __init__(self) -> None:
        self.session: Session | None = None
        self._run_id: str | None = None
        self._pending: list[_Delivery] = []

    def fire(self, text: str) -> asyncio.Future[None]:
        assert self.session is not None
        delivery = _Delivery(text, asyncio.get_running_loop().create_future())
        self._pending.append(delivery)
        delivery.ended.add_done_callback(lambda _: self._pending.remove(delivery))
        try:
            self.session.follow_up(text, source="harness")
        except BaseException:
            delivery.ended.cancel()
            raise
        return delivery.ended

    def observe(self, event: Event) -> None:
        match event:
            case RunStart(run_id=run_id):
                self._run_id = run_id
            case MessageEvent(message=UserMessage(content=text, source="harness")):
                for delivery in self._pending:
                    if delivery.text == text and delivery.run_id is None and not delivery.ended.done():
                        delivery.run_id = self._run_id
                        break
            case RunEnd(run_id=run_id, reason=reason):
                for delivery in self._pending:
                    # Esc clears Session's queued follow-ups. Complete their waits too, so repeats re-arm.
                    if (
                        delivery.run_id == run_id or (delivery.run_id is None and reason == "aborted")
                    ) and not delivery.ended.done():
                        delivery.ended.set_result(None)
                self._run_id = None
