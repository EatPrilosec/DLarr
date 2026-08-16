import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from backend.app.main import app
from backend.app.services.concurrency_manager import ConcurrencyManager, concurrency_manager
from backend.app.core.database import AsyncSessionLocal
from backend.app.models.job import Job


@pytest.mark.asyncio
async def test_concurrency_manager_limits():
    mgr = ConcurrencyManager(max_jobs=2, max_ollama=2)
    assert mgr.max_jobs == 2
    assert mgr.max_ollama == 2

    # Dynamic limits resize
    mgr.update_limits(max_jobs=4, max_ollama=3)
    assert mgr.max_jobs == 4
    assert mgr.max_ollama == 3


@pytest.mark.asyncio
async def test_concurrency_manager_slots():
    mgr = ConcurrencyManager(max_jobs=1, max_ollama=1)
    executed = []

    async def worker(idx: int):
        async with mgr.job_slot():
            executed.append(f"start-{idx}")
            await asyncio.sleep(0.05)
            executed.append(f"end-{idx}")

    await asyncio.gather(worker(1), worker(2))
    # Serialized execution: start-1 -> end-1 -> start-2 -> end-2
    assert executed == ["start-1", "end-1", "start-2", "end-2"]


@pytest.mark.asyncio
async def test_job_cancellation_registry():
    mgr = ConcurrencyManager(max_jobs=1, max_ollama=1)

    async def long_running_task():
        await asyncio.sleep(10.0)

    task = asyncio.create_task(long_running_task())
    mgr.register_task(999, task)

    assert not mgr.is_cancelled(999)
    assert not task.done()

    # Cancel job
    result = mgr.cancel_job(999)
    assert result is True
    assert mgr.is_cancelled(999)

    # Let event loop process task cancellation
    await asyncio.sleep(0.01)
    assert task.cancelled() or task.done()
    mgr.unregister_task(999)
    assert not mgr.is_cancelled(999)


@pytest.mark.asyncio
async def test_cancel_job_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncSessionLocal() as db:
        test_job = Job(
            job_type="IMPORT_SHOW",
            status="RUNNING",
            progress=25.0,
            message="Running matching pass..."
        )
        db.add(test_job)
        await db.commit()
        await db.refresh(test_job)
        job_id = test_job.id

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True

        # Check job status in DB
        res_job = await client.get(f"/api/v1/jobs/{job_id}")
        assert res_job.status_code == 200
        assert res_job.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_settings_concurrency_fields():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Get settings
        res = await client.get("/api/v1/settings")
        assert res.status_code == 200
        settings = res.json()
        assert "max_concurrent_jobs" in settings
        assert "max_concurrent_ollama_requests" in settings

        # Update settings
        settings["max_concurrent_jobs"] = 3
        settings["max_concurrent_ollama_requests"] = 2
        res_post = await client.post("/api/v1/settings", json=settings)
        assert res_post.status_code == 200
        assert res_post.json()["max_concurrent_jobs"] == 3
        assert res_post.json()["max_concurrent_ollama_requests"] == 2

        # Verify concurrency manager received updated limits
        assert concurrency_manager.max_jobs == 3
        assert concurrency_manager.max_ollama == 2
