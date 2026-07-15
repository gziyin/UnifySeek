from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any
from uuid import UUID

from ai_dev_researcher.domain.events import EventType, ResearchEvent
from ai_dev_researcher.repositories.events import EventRepository


class EventPublisher:
    """Persist-first event publisher with per-run fanout queues."""

    def __init__(self, events: EventRepository, *, queue_size: int = 256):
        self._events = events
        self._queue_size = queue_size
        self._subscribers: dict[UUID, set[asyncio.Queue[ResearchEvent | None]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        event_type: EventType,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> ResearchEvent:
        event = await self._events.append(
            session_id=session_id,
            run_id=run_id,
            event_type=event_type,
            actor=actor,
            payload=payload or {},
        )
        async with self._lock:
            queues = list(self._subscribers.get(run_id, set()))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: signal disconnect via sentinel.
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
        return event

    async def subscribe(self, run_id: UUID) -> asyncio.Queue[ResearchEvent | None]:
        queue: asyncio.Queue[ResearchEvent | None] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers[run_id].add(queue)
        return queue

    async def unsubscribe(self, run_id: UUID, queue: asyncio.Queue[ResearchEvent | None]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(run_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(run_id, None)
