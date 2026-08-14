import asyncio
from collections import defaultdict
from collections.abc import Callable
from threading import Lock


class ProjectSubscription:
    def __init__(
        self,
        project_id: str,
        broker: "ProjectEventBroker",
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.project_id = project_id
        self._broker = broker
        self._loop = loop
        self._queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._closed = False

    async def wait(self, timeout_seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._queue.get(), timeout=timeout_seconds)
        except TimeoutError:
            return False
        return True

    def notify(self) -> None:
        if self._closed or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._enqueue)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._broker.unsubscribe(self)

    def _enqueue(self) -> None:
        if self._closed or self._queue.full():
            return
        self._queue.put_nowait(None)


class ProjectEventBroker:
    """In-process change signals; persisted project state remains authoritative."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._subscribers: dict[str, set[ProjectSubscription]] = defaultdict(set)

    def subscribe(self, project_id: str) -> ProjectSubscription:
        subscription = ProjectSubscription(
            project_id, self, asyncio.get_running_loop()
        )
        with self._lock:
            self._subscribers[project_id].add(subscription)
        return subscription

    def unsubscribe(self, subscription: ProjectSubscription) -> None:
        with self._lock:
            subscribers = self._subscribers.get(subscription.project_id)
            if subscribers is None:
                return
            subscribers.discard(subscription)
            if not subscribers:
                self._subscribers.pop(subscription.project_id, None)

    def publish(self, project_id: str) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers.get(project_id, ()))
        for subscription in subscribers:
            subscription.notify()

    def subscriber_count(self, project_id: str) -> int:
        with self._lock:
            return len(self._subscribers.get(project_id, ()))


ProjectChangeNotifier = Callable[[str], None]


def ignore_project_change(project_id: str) -> None:
    del project_id
