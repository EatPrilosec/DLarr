import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from backend.app.main import app
from backend.app.core.database import init_db


@pytest.fixture(scope="session", autouse=True)
def init_test_database():
    asyncio.run(init_db())


@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["app"] == "DLarr"


@pytest.mark.asyncio
async def test_settings_crud():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Get default settings
        res = await client.get("/api/v1/settings")
        assert res.status_code == 200
        settings = res.json()
        assert "ollama_url" in settings

        # Update settings
        settings["ollama_primary_model"] = "qwen2.5:7b"
        post_res = await client.post("/api/v1/settings", json=settings)
        assert post_res.status_code == 200
        assert post_res.json()["ollama_primary_model"] == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_spa_serving():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/")
        assert res.status_code == 200
        assert "<div id=\"root\"></div>" in res.text
