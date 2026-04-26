"""
Shared task manager for AutoPattern.

Provides a centralized registry of all automation tasks — whether
triggered from the CLI REPL or the REST API — so both subsystems
share visibility and lifecycle management.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional
from uuid import uuid4

logger = logging.getLogger("autopattern.tasks")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskSource(Enum):
    CLI = "cli"
    API = "api"


class TaskStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Task metadata
# ---------------------------------------------------------------------------

@dataclass
class TaskInfo:
    """Metadata for a tracked automation task."""

    id: str
    description: str
    source: TaskSource
    status: TaskStatus = TaskStatus.RUNNING
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    asyncio_task: Optional[asyncio.Task] = None
    result: Optional[dict] = None


# Callback type alias
TaskCallback = Callable[["TaskInfo"], None]


class BusyError(Exception):
    """Raised when a task is submitted while another is already running."""


# ---------------------------------------------------------------------------
# TaskManager
# ---------------------------------------------------------------------------

class TaskManager:
    """Centralized task registry shared between CLI and API server.

    Only one automation task may run at a time (they share the browser).
    Use :meth:`acquire` / :meth:`release` or the :attr:`lock` directly.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._on_start: list[TaskCallback] = []
        self._on_end: list[TaskCallback] = []
        self._lock: Optional[asyncio.Lock] = None  # lazily created

    @property
    def lock(self) -> asyncio.Lock:
        """Return the concurrency lock (created lazily inside the running loop)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def is_busy(self) -> bool:
        """True when a task is currently running."""
        return self.lock.locked()

    # -- Callbacks --------------------------------------------------------

    def on_task_start(self, callback: TaskCallback) -> None:
        """Register a callback invoked when any task starts."""
        self._on_start.append(callback)

    def on_task_end(self, callback: TaskCallback) -> None:
        """Register a callback invoked when any task ends."""
        self._on_end.append(callback)

    # -- Queries ----------------------------------------------------------

    @property
    def active_tasks(self) -> list[TaskInfo]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.RUNNING]

    @property
    def active_count(self) -> int:
        return len(self.active_tasks)

    @property
    def all_tasks(self) -> list[TaskInfo]:
        return list(self._tasks.values())

    # -- Submit / Cancel --------------------------------------------------

    def submit(
        self,
        coro,
        description: str,
        source: TaskSource,
    ) -> TaskInfo:
        """Submit an automation coroutine for tracked execution.

        Acquires the concurrency lock so only one task runs at a time.
        Returns a `TaskInfo` whose `.asyncio_task` can be awaited to
        obtain the result dict from the runner.

        Raises ``BusyError`` if another task is already running.
        """
        if self.is_busy:
            running = self.active_tasks
            msg = "Another task is already running"
            if running:
                msg += f" [{running[0].source.value}]: {running[0].description[:60]}"
            raise BusyError(msg)

        task_id = uuid4().hex[:8]
        info = TaskInfo(
            id=task_id,
            description=description,
            source=source,
        )

        async def _wrapper():
            async with self.lock:
                try:
                    result = await coro
                    info.result = result
                    info.status = (
                        TaskStatus.COMPLETED
                        if result.get("success")
                        else TaskStatus.FAILED
                    )
                    return result
                except asyncio.CancelledError:
                    info.status = TaskStatus.CANCELLED
                    raise
                except Exception as e:
                    info.status = TaskStatus.FAILED
                    result = {"success": False, "error": str(e)}
                    info.result = result
                    return result
                finally:
                    info.finished_at = datetime.now()
                    self._fire(self._on_end, info)

        info.asyncio_task = asyncio.create_task(_wrapper())
        self._tasks[task_id] = info

        logger.info(
            "Task %s submitted [%s]: %s",
            task_id,
            source.value,
            description[:80],
        )
        self._fire(self._on_start, info)
        return info

    def cancel(self, task_id: str) -> bool:
        """Cancel a running task by ID. Returns True if cancellation was sent."""
        info = self._tasks.get(task_id)
        if info and info.asyncio_task and not info.asyncio_task.done():
            info.asyncio_task.cancel()
            return True
        return False

    def cancel_all(self, source: Optional[TaskSource] = None) -> None:
        """Cancel all active tasks, optionally filtered by source."""
        for info in self.active_tasks:
            if source is None or info.source == source:
                self.cancel(info.id)

    async def drain(self, timeout: float = 10.0) -> None:
        """Wait for active tasks to finish; cancel stragglers after *timeout*."""
        active = self.active_tasks
        if not active:
            return

        logger.info("Draining %d active task(s)...", len(active))
        tasks = [t.asyncio_task for t in active if t.asyncio_task]
        if not tasks:
            return

        done, pending = await asyncio.wait(tasks, timeout=timeout)

        if pending:
            logger.warning(
                "Cancelling %d task(s) that did not finish in time.",
                len(pending),
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    # -- Internal ---------------------------------------------------------

    @staticmethod
    def _fire(callbacks: list[TaskCallback], info: TaskInfo) -> None:
        for cb in callbacks:
            try:
                cb(info)
            except Exception:
                logger.debug("Task callback error", exc_info=True)


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------

task_manager = TaskManager()
