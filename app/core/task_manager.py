"""Task manager for background work using QThread workers.

Provides a centralized way to run CPU-bound or I/O-bound tasks
without blocking the PySide6 UI thread.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any, Callable

from loguru import logger
from PySide6.QtCore import QObject, QThread, Signal


class TaskStatus(Enum):
    """Status of a managed task."""

    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


class WorkerSignals(QObject):
    """Signals emitted by background workers."""

    started = Signal()
    progress = Signal(int, str)  # percent, message
    completed = Signal(object)  # result
    failed = Signal(str)  # error message
    cancelled = Signal()


class Worker(QThread):
    """Generic background worker that runs a callable in a separate thread.

    Usage:
        worker = Worker(my_function, arg1, arg2, kwarg1=val)
        worker.signals.completed.connect(on_done)
        worker.signals.failed.connect(on_error)
        worker.start()
    """

    def __init__(
        self,
        fn: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._is_cancelled = False
        self.status = TaskStatus.PENDING

    def run(self) -> None:
        """Execute the worker function."""
        self.status = TaskStatus.RUNNING
        self.signals.started.emit()
        try:
            if self._is_cancelled:
                self.status = TaskStatus.CANCELLED
                self.signals.cancelled.emit()
                return

            result = self.fn(*self.args, **self.kwargs)

            if self._is_cancelled:
                self.status = TaskStatus.CANCELLED
                self.signals.cancelled.emit()
                return

            self.status = TaskStatus.COMPLETED
            self.signals.completed.emit(result)
        except Exception as e:
            self.status = TaskStatus.FAILED
            logger.exception("Worker failed: {}", str(e))
            self.signals.failed.emit(str(e))

    def cancel(self) -> None:
        """Request cancellation of the worker."""
        self._is_cancelled = True
        logger.info("Worker cancellation requested")

    @property
    def is_cancelled(self) -> bool:
        return self._is_cancelled


class TaskManager(QObject):
    """Manages a pool of background workers with concurrency limits.

    Provides lifecycle management, progress tracking, and cleanup.
    """

    task_started = Signal(str)  # task_id
    task_completed = Signal(str, object)  # task_id, result
    task_failed = Signal(str, str)  # task_id, error
    task_progress = Signal(str, int, str)  # task_id, percent, message

    def __init__(self, max_concurrent: int = 5, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._max_concurrent = max_concurrent
        self._active_workers: dict[str, Worker] = {}
        self._pending_tasks: list[tuple[str, Worker]] = []
        self._task_counter = 0

    def submit(
        self,
        fn: Callable,
        *args: Any,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Submit a task for background execution.

        Args:
            fn: The callable to execute.
            *args: Positional arguments for fn.
            task_id: Optional custom task ID. Auto-generated if not provided.
            **kwargs: Keyword arguments for fn.

        Returns:
            The task ID string.
        """
        if task_id is None:
            self._task_counter += 1
            task_id = f"task_{self._task_counter}"

        worker = Worker(fn, *args, **kwargs)
        worker.signals.started.connect(lambda: self._on_started(task_id))
        worker.signals.completed.connect(lambda r: self._on_completed(task_id, r))
        worker.signals.failed.connect(lambda e: self._on_failed(task_id, e))
        worker.signals.progress.connect(lambda p, m: self.task_progress.emit(task_id, p, m))

        if len(self._active_workers) < self._max_concurrent:
            self._active_workers[task_id] = worker
            worker.start()
            logger.info("Task started: {}", task_id)
        else:
            self._pending_tasks.append((task_id, worker))
            logger.info("Task queued: {} (active: {})", task_id, len(self._active_workers))

        return task_id

    def cancel(self, task_id: str) -> bool:
        """Cancel a task by ID.

        Args:
            task_id: The task to cancel.

        Returns:
            True if the task was found and cancellation was requested.
        """
        if task_id in self._active_workers:
            self._active_workers[task_id].cancel()
            logger.info("Task cancel requested: {}", task_id)
            return True

        for i, (tid, worker) in enumerate(self._pending_tasks):
            if tid == task_id:
                worker.cancel()
                self._pending_tasks.pop(i)
                logger.info("Pending task cancelled: {}", task_id)
                return True

        return False

    def cancel_all(self) -> None:
        """Cancel all active and pending tasks."""
        for task_id, worker in list(self._active_workers.items()):
            worker.cancel()
        for task_id, worker in self._pending_tasks:
            worker.cancel()
        self._pending_tasks.clear()
        logger.info("All tasks cancelled")

    def shutdown(self) -> None:
        """Cancel all tasks and wait for active workers to finish."""
        self.cancel_all()
        for task_id, worker in list(self._active_workers.items()):
            if worker.isRunning():
                worker.quit()
                worker.wait(5000)  # Wait up to 5 seconds
        self._active_workers.clear()
        logger.info("TaskManager shutdown complete")

    @property
    def active_count(self) -> int:
        return len(self._active_workers)

    @property
    def pending_count(self) -> int:
        return len(self._pending_tasks)

    def _on_started(self, task_id: str) -> None:
        self.task_started.emit(task_id)

    def _on_completed(self, task_id: str, result: Any) -> None:
        self._active_workers.pop(task_id, None)
        self.task_completed.emit(task_id, result)
        self._start_next()

    def _on_failed(self, task_id: str, error: str) -> None:
        self._active_workers.pop(task_id, None)
        self.task_failed.emit(task_id, error)
        self._start_next()

    def _start_next(self) -> None:
        """Start the next pending task if capacity is available."""
        while self._pending_tasks and len(self._active_workers) < self._max_concurrent:
            task_id, worker = self._pending_tasks.pop(0)
            if not worker.is_cancelled:
                self._active_workers[task_id] = worker
                worker.start()
                logger.info("Pending task started: {}", task_id)
