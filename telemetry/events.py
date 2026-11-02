"""In-process fan-out for live task events.

Events are persisted in PostgreSQL by the repository. This bus only wakes live
SSE subscribers; replay comes from the database, so a restart does not lose
history.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Dict, Set

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[task_id].add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(task_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(task_id, None)

    async def publish(self, task_id: str, event: dict) -> None:
        for queue in list(self._subscribers.get(task_id, set())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:  # noqa: BLE001
                    log.warning("dropping event for task %s: subscriber queue full", task_id)

    async def close(self, task_id: str) -> None:
        for queue in list(self._subscribers.get(task_id, set())):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
