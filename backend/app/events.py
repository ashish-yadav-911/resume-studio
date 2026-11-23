"""Run event persistence + in-process pub/sub for resumable SSE streams."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import RunEvent

logger = logging.getLogger("resume_studio.events")

TERMINAL_EVENTS = {"run.completed", "run.failed", "run.awaiting_approval", "run.clarification", "run.rejected"}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers[run_id].add(queue)
        return queue

    async def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers[run_id].discard(queue)

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(run_id, ()))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # slow consumer: skip; replay covers it
                logger.warning("SSE subscriber queue full for run %s", run_id)

    async def emit(
        self,
        db: Session,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        payload = payload or {}
        seq = int(
            db.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)) or 0
        ) + 1
        event = RunEvent(run_id=run_id, seq=seq, type=event_type, payload=payload)
        db.add(event)
        if commit:
            db.commit()
        event_data = {
            "seq": seq,
            "run_id": run_id,
            "type": event_type,
            "payload": payload,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        await self.publish(run_id, event_data)
        return event_data


bus = EventBus()


def replay_events(run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        events = db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
            .order_by(RunEvent.seq)
        ).all()
        return [
            {
                "seq": e.seq,
                "run_id": e.run_id,
                "type": e.type,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]
