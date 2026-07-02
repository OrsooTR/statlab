"""Background-job manager.

Long-running work (Monte Carlo simulations, data refreshes, backtests) is executed
off the event loop and exposed to the UI through a polling API with live progress.

CPU-bound jobs run in a ProcessPoolExecutor; progress is streamed back through a
multiprocessing queue that a watcher thread drains. I/O-bound jobs run in threads.
"""
from __future__ import annotations

import multiprocessing as mp
import threading
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import MAX_WORKERS


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"          # running | done | error
    progress: float = 0.0            # 0..1
    message: str = ""
    result: Any = None
    error: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out = {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "progress": round(self.progress, 4),
                "message": self.message,
                "error": self.error,
            }
            if self.status == "done":
                out["result"] = self.result
            return out


class JobManager:
    """Singleton-style manager shared by both modules."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self._threads = ThreadPoolExecutor(max_workers=8, thread_name_prefix="statlab-io")
        self._procs: Optional[ProcessPoolExecutor] = None
        self._mp_ctx = mp.get_context("spawn")

    # -- lifecycle -----------------------------------------------------------
    def _process_pool(self) -> ProcessPoolExecutor:
        if self._procs is None:
            self._procs = ProcessPoolExecutor(max_workers=MAX_WORKERS, mp_context=self._mp_ctx)
        return self._procs

    def shutdown(self) -> None:
        self._threads.shutdown(wait=False, cancel_futures=True)
        if self._procs is not None:
            self._procs.shutdown(wait=False, cancel_futures=True)

    # -- public API ----------------------------------------------------------
    def get(self, job_id: str) -> Optional[Job]:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def submit_thread(self, kind: str, fn: Callable[..., Any], *args: Any,
                      on_done: Optional[Callable[[Any], Any]] = None, **kwargs: Any) -> Job:
        """Run fn(job_progress_cb, *args, **kwargs) in a thread.

        fn receives a callable `progress(frac, message)` as its first argument.
        on_done(result) runs after success (e.g. persistence).
        """
        job = self._new_job(kind)

        def progress(frac: float, message: str = "") -> None:
            with job._lock:
                job.progress = float(frac)
                if message:
                    job.message = message

        def runner() -> None:
            try:
                result = fn(progress, *args, **kwargs)
                if on_done is not None:
                    result = on_done(result) or result
                with job._lock:
                    job.result = result
                    job.progress = 1.0
                    job.status = "done"
            except Exception as exc:  # noqa: BLE001 - report any failure to the UI
                with job._lock:
                    job.status = "error"
                    job.error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()

        self._threads.submit(runner)
        return job

    def submit_process(self, kind: str, fn: Callable[..., Any], *args: Any,
                       on_done: Optional[Callable[[Any], Any]] = None) -> Job:
        """Run fn(progress_queue, *args) in a worker process.

        fn must be a module-level function (picklable). It may push
        (frac, message) tuples onto the queue; its return value becomes the
        job result.
        """
        job = self._new_job(kind)
        queue = self._mp_ctx.Manager().Queue()
        future = self._process_pool().submit(fn, queue, *args)

        def watcher() -> None:
            # Drain progress until the future resolves.
            while not future.done():
                try:
                    frac, message = queue.get(timeout=0.25)
                    with job._lock:
                        job.progress = float(frac)
                        if message:
                            job.message = message
                except Exception:
                    pass
            try:
                result = future.result()
                if on_done is not None:
                    result = on_done(result) or result
                with job._lock:
                    job.result = result
                    job.progress = 1.0
                    job.status = "done"
            except Exception as exc:  # noqa: BLE001
                with job._lock:
                    job.status = "error"
                    job.error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()

        threading.Thread(target=watcher, daemon=True, name=f"job-{job.id[:8]}").start()
        return job

    def submit_process_fanout(self, kind: str, fn: Callable[..., Any],
                              args_list: list[tuple], aggregate: Callable[[list[Any]], Any],
                              on_done: Optional[Callable[[Any], Any]] = None) -> Job:
        """Run fn(*args) for every args tuple on the shared process pool,
        report progress as tasks complete, then aggregate the results."""
        job = self._new_job(kind)
        pool = self._process_pool()

        def runner() -> None:
            try:
                futures = [pool.submit(fn, *args) for args in args_list]
                results: list[Any] = [None] * len(futures)
                done_count = 0
                for i, fut in enumerate(futures):
                    results[i] = fut.result()
                    done_count += 1
                    with job._lock:
                        job.progress = done_count / len(futures)
                        job.message = f"{done_count}/{len(futures)} runs complete"
                agg = aggregate(results)
                if on_done is not None:
                    agg = on_done(agg) or agg
                with job._lock:
                    job.result = agg
                    job.progress = 1.0
                    job.status = "done"
            except Exception as exc:  # noqa: BLE001
                with job._lock:
                    job.status = "error"
                    job.error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()

        threading.Thread(target=runner, daemon=True, name=f"job-{job.id[:8]}").start()
        return job

    # -- internals -----------------------------------------------------------
    def _new_job(self, kind: str) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind)
        with self._jobs_lock:
            self._jobs[job.id] = job
            # Keep the registry bounded.
            if len(self._jobs) > 200:
                done = [k for k, v in self._jobs.items() if v.status != "running"]
                for k in done[:-100]:
                    self._jobs.pop(k, None)
        return job


manager = JobManager()
