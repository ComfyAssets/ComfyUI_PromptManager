"""Progress fan-out for the background output scan.

The scan runs as an asyncio task that outlives the HTTP request that started
it, so an accidental tab close does not abandon a half-finished scan. Every
SSE subscriber (the request that started the scan, a reconnecting page, a
second tab) receives the latest event as a snapshot and then every event
published after it, until the job finishes.
"""

import asyncio


class ScanJob:
    """One scan run: its latest event, completion flag and live subscribers."""

    def __init__(self):
        self.last_event = None
        self.finished = False
        self.task = None
        self._subscribers = set()

    @property
    def running(self):
        return not self.finished

    @property
    def subscriber_count(self):
        return len(self._subscribers)

    def publish(self, payload):
        """Record ``payload`` as the latest event and hand it to every subscriber."""
        self.last_event = payload
        for queue in list(self._subscribers):
            queue.put_nowait(payload)

    def finish(self):
        """Mark the job done and release every subscriber after its queued events."""
        self.finished = True
        for queue in list(self._subscribers):
            queue.put_nowait(None)

    async def events(self):
        """Yield the latest event, then each later one, until the job finishes.

        Consume it under ``contextlib.aclosing`` so a consumer that stops early
        (a closed socket) drops its queue at once instead of at garbage collection.
        """
        queue = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            if self.last_event is not None:
                yield self.last_event
            if self.finished:
                return
            while True:
                payload = await queue.get()
                if payload is None:
                    return
                yield payload
        finally:
            self._subscribers.discard(queue)
