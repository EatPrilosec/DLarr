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
        # Get current settings
        res = await client.get("/api/v1/settings")
        assert res.status_code == 200
        original_settings = res.json()
        assert "ollama_url" in original_settings

        try:
            # Test update settings
            updated_payload = dict(original_settings)
            updated_payload["ollama_primary_model"] = "test-model:latest"
            post_res = await client.post("/api/v1/settings", json=updated_payload)
            assert post_res.status_code == 200
            assert post_res.json()["ollama_primary_model"] == "test-model:latest"
        finally:
            # Restore original settings to never overwrite user configuration
            await client.post("/api/v1/settings", json=original_settings)


@pytest.mark.asyncio
async def test_spa_serving():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/")
        assert res.status_code == 200
        assert "<div id=\"root\"></div>" in res.text
