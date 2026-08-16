import pytest
import json
import random
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport

from backend.app.main import app
from backend.app.models.show import Show, Episode, EpisodeSourceMetadata
from backend.app.models.job import Job
from backend.app.services.matching_engine import MatchingEngine
from backend.app.core.database import AsyncSessionLocal, init_db


@pytest.mark.asyncio
async def test_rescan_show_endpoint():
    await init_db()
    s_id = random.randint(100000, 999999)
    async with AsyncSessionLocal() as db:
        show = Show(sonarr_series_id=s_id, title="Rescan Test Show")
        db.add(show)
        await db.commit()
        await db.refresh(show)
        show_id = show.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(f"/api/v1/shows/{show_id}/rescan", json={
            "scan_mode": "custom",
            "sources": ["tmdb"]
        })
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "job_id" in data

        job_id = data["job_id"]
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            assert job is not None
            payload = json.loads(job.payload)
            assert payload["scan_mode"] == "custom"
            assert payload["sources"] == ["tmdb"]


@pytest.mark.asyncio
async def test_rescan_season_endpoint():
    s_id = random.randint(100000, 999999)
    async with AsyncSessionLocal() as db:
        show = Show(sonarr_series_id=s_id, title="Season Rescan Test Show")
        db.add(show)
        await db.commit()
        await db.refresh(show)
        show_id = show.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(f"/api/v1/shows/{show_id}/seasons/2/rescan", json={
            "scan_mode": "full",
            "sources": ["tmdb", "tvmaze"]
        })
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True

        job_id = data["job_id"]
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            payload = json.loads(job.payload)
            assert payload["season_number"] == 2
            assert payload["sources"] == ["tmdb", "tvmaze"]


@pytest.mark.asyncio
async def test_rescan_episode_endpoint():
    s_id = random.randint(100000, 999999)
    async with AsyncSessionLocal() as db:
        show = Show(sonarr_series_id=s_id, title="Episode Rescan Test Show")
        db.add(show)
        await db.flush()
        ep = Episode(show_id=show.id, sonarr_episode_id=s_id * 10, season_number=1, episode_number=1, title="Test Ep")
        db.add(ep)
        await db.commit()
        await db.refresh(ep)
        ep_id = ep.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(f"/api/v1/episodes/{ep_id}/rescan", json={
            "sources": ["omdb"]
        })
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True

        job_id = data["job_id"]
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            payload = json.loads(job.payload)
            assert payload["episode_id"] == ep_id
            assert payload["sources"] == ["omdb"]


@pytest.mark.asyncio
async def test_restart_cancelled_job():
    s_id = random.randint(100000, 999999)
    async with AsyncSessionLocal() as db:
        job = Job(
            job_type="AI_MATCHING",
            status="CANCELLED",
            payload=json.dumps({"sonarr_series_id": s_id, "show_id": 1, "scan_mode": "full", "sources": ["tmdb"]})
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    with patch("backend.app.api.v1.endpoints.shows.run_import_pipeline", new_callable=AsyncMock):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(f"/api/v1/jobs/{job_id}/restart")
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True

            async with AsyncSessionLocal() as db:
                restarted_job = await db.get(Job, job_id)
                assert restarted_job.status == "PENDING"
                assert restarted_job.progress == 0.0


@pytest.mark.asyncio
async def test_process_show_ingestion_no_scan():
    s_id = random.randint(100000, 999999)
    fake_series = {
        "id": s_id,
        "title": "No Scan Show",
        "cleanTitle": "noscan",
        "overview": "Test",
        "images": []
    }
    fake_episodes = [
        {"id": s_id * 10, "seasonNumber": 1, "episodeNumber": 1, "title": "Ep 1", "overview": "Plot 1"}
    ]

    with patch("backend.app.services.sonarr_client.SonarrClient.get_series_detail", new_callable=AsyncMock) as mock_s, \
         patch("backend.app.services.sonarr_client.SonarrClient.get_episodes", new_callable=AsyncMock) as mock_eps:
        mock_s.return_value = fake_series
        mock_eps.return_value = fake_episodes

        async with AsyncSessionLocal() as db:
            show = await MatchingEngine.process_show_ingestion(
                db=db,
                sonarr_series_id=s_id,
                config={"sonarr_url": "http://mock", "sonarr_api_key": "mock"},
                scan_mode="none"
            )
            assert show is not None
            assert show.title == "No Scan Show"


@pytest.mark.asyncio
async def test_process_show_ingestion_single_source_custom_scan():
    s_id = random.randint(100000, 999999)
    fake_series = {
        "id": s_id,
        "title": "Single Source Show",
        "cleanTitle": "singlesource",
        "overview": "Test",
        "images": []
    }
    fake_episodes = [
        {"id": s_id * 10, "seasonNumber": 1, "episodeNumber": 1, "title": "Ep 1", "overview": "Plot 1"}
    ]

    with patch("backend.app.services.sonarr_client.SonarrClient.get_series_detail", new_callable=AsyncMock) as mock_s, \
         patch("backend.app.services.sonarr_client.SonarrClient.get_episodes", new_callable=AsyncMock) as mock_eps, \
         patch("backend.app.services.tmdb_client.TMDBClient.find_by_external_id", new_callable=AsyncMock) as mock_tmdb_find, \
         patch("backend.app.services.tvmaze_client.TVmazeClient.lookup_show", new_callable=AsyncMock) as mock_tvmaze:
        mock_s.return_value = fake_series
        mock_eps.return_value = fake_episodes
        mock_tmdb_find.return_value = 99999
        mock_tvmaze.return_value = None

        async with AsyncSessionLocal() as db:
            show = await MatchingEngine.process_show_ingestion(
                db=db,
                sonarr_series_id=s_id,
                config={"sonarr_url": "http://mock", "sonarr_api_key": "mock", "tmdb_api_key": "mock_tmdb"},
                scan_mode="custom",
                selected_sources=["tmdb"]
            )
            assert show is not None
            # TVmaze must NOT be queried when only tmdb is selected
            mock_tvmaze.assert_not_called()
