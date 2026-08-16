import asyncio
from typing import Dict, Set, Optional
from contextlib import asynccontextmanager


class ConcurrencyManager:
    def __init__(self, max_jobs: int = 1, max_ollama: int = 1):
        self.max_jobs = max_jobs
        self.max_ollama = max_ollama
        self._job_semaphore = asyncio.Semaphore(max_jobs)
        self._ollama_semaphore = asyncio.Semaphore(max_ollama)
        self._active_tasks: Dict[int, asyncio.Task] = {}
        self._cancelled_jobs: Set[int] = set()

    def update_limits(self, max_jobs: int, max_ollama: int):
        """Dynamically update concurrency semaphores."""
        if max_jobs != self.max_jobs and max_jobs >= 1:
            self.max_jobs = max_jobs
            self._job_semaphore = asyncio.Semaphore(max_jobs)
        if max_ollama != self.max_ollama and max_ollama >= 1:
            self.max_ollama = max_ollama
            self._ollama_semaphore = asyncio.Semaphore(max_ollama)

    def register_task(self, job_id: int, task: asyncio.Task):
        self._active_tasks[job_id] = task

    def unregister_task(self, job_id: int):
        self._active_tasks.pop(job_id, None)
        self._cancelled_jobs.discard(job_id)

    def cancel_job(self, job_id: int) -> bool:
        """Flags job as cancelled and cancels the underlying asyncio task if running."""
        self._cancelled_jobs.add(job_id)
        task = self._active_tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def is_cancelled(self, job_id: Optional[int]) -> bool:
        if job_id is None:
            return False
        return job_id in self._cancelled_jobs

    @asynccontextmanager
    async def job_slot(self):
        async with self._job_semaphore:
            yield

    @asynccontextmanager
    async def ollama_slot(self):
        async with self._ollama_semaphore:
            yield


# Global singleton instance
concurrency_manager = ConcurrencyManager(max_jobs=1, max_ollama=1)
