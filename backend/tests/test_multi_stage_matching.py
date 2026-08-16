import pytest
import json
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport

from backend.app.main import app
from backend.app.models.show import Show, Episode, EpisodeSourceMetadata
from backend.app.services.matching_engine import MatchingEngine
from backend.app.core.database import AsyncSessionLocal


@pytest.mark.asyncio
async def test_step0_no_match_dual_agreement():
    sonarr_eps = [
        Episode(season_number=0, episode_number=1, title="Special 1"),
        Episode(season_number=0, episode_number=2, title="Special 2"),
        Episode(season_number=1, episode_number=1, title="Episode 1"),
    ]
    source_eps = [
        {"season": 1, "episode": 1, "title": "Episode 1"},
    ]

    with patch("backend.app.services.ollama_client.OllamaClient.query_with_retry_and_fallback") as mock_query:
        # Primary says S00E01, S00E02 are missing. Fallback says S00E01, S00E02 are missing.
        mock_query.side_effect = [
            ("S00E01, S00E02", "primary"),
            ("S00E01, S00E02", "fallback")
        ]

        res = await MatchingEngine.ai_step0_detect_no_match(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model="fallback",
            show_title="Test Show",
            source_name="TMDB",
            sonarr_eps=sonarr_eps,
            source_eps=source_eps
        )
        assert res == {(0, 1), (0, 2)}


@pytest.mark.asyncio
async def test_step1_batch_confirm_all_yes():
    batch_pairs = [
        (Episode(id=1, season_number=1, episode_number=1, title="Ep 1"), {"season": 1, "episode": 1, "title": "Ep 1"}),
        (Episode(id=2, season_number=1, episode_number=2, title="Ep 2"), {"season": 1, "episode": 2, "title": "Ep 2"}),
    ]

    with patch("backend.app.services.ollama_client.OllamaClient.query_with_retry_and_fallback") as mock_query:
        mock_query.return_value = ("yes", "primary")

        confirmed_ids, _ = await MatchingEngine.ai_batch_confirm_matches(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model="fallback",
            show_title="Test Show",
            source_name="TMDB",
            batch_pairs=batch_pairs
        )
        assert confirmed_ids == {1, 2}


@pytest.mark.asyncio
async def test_step1_batch_confirm_partial_failure():
    batch_pairs = [
        (Episode(id=10, season_number=1, episode_number=1, title="Ep 1"), {"season": 1, "episode": 1, "title": "Ep 1"}),
        (Episode(id=20, season_number=1, episode_number=2, title="Ep 2"), {"season": 1, "episode": 2, "title": "Wrong Story"}),
    ]

    with patch("backend.app.services.ollama_client.OllamaClient.query_with_retry_and_fallback") as mock_query:
        # LLM reports S01E02 as a failure
        mock_query.return_value = ("S01E02", "primary")

        confirmed_ids, _ = await MatchingEngine.ai_batch_confirm_matches(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model="fallback",
            show_title="Test Show",
            source_name="TMDB",
            batch_pairs=batch_pairs
        )
        # Episode 10 confirmed, Episode 20 rejected
        assert confirmed_ids == {10}


@pytest.mark.asyncio
async def test_step2_ai_search_candidates():
    sonarr_ep = Episode(id=1, season_number=1, episode_number=5, title="Lost Flight")
    candidates = [
        {"season": 2, "episode": 3, "title": "Lost Flight", "overview": "A plane goes missing"},
    ]

    with patch("backend.app.services.ollama_client.OllamaClient.query_with_retry_and_fallback") as mock_query:
        mock_query.return_value = ("S02E03", "primary")

        res, _ = await MatchingEngine.ai_search_candidates(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model="fallback",
            show_title="Test Show",
            canonical_ep=sonarr_ep,
            candidates=candidates,
            source_name="TMDB"
        )
        assert res == (2, 3)


@pytest.mark.asyncio
async def test_manual_match_and_mark_no_match_api():
    import random
    s_id = random.randint(100000, 999999)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Seed an episode in DB
        async with AsyncSessionLocal() as db:
            show = Show(sonarr_series_id=s_id, title="Test Audit Show")
            db.add(show)
            await db.flush()

            ep = Episode(
                show_id=show.id,
                sonarr_episode_id=s_id * 10,
                season_number=1,
                episode_number=1,
                title="Pilot"
            )
            db.add(ep)
            await db.commit()
            ep_id = ep.id

        # Test Manual Match
        resp = await ac.post(f"/api/v1/episodes/{ep_id}/manual-match", json={
            "source_name": "tmdb",
            "source_season_number": 1,
            "source_episode_number": 1,
            "title": "Pilot Episode"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ai_verification_status"] == "MANUAL_VERIFIED"
        assert len(data["source_variations"]) >= 1

        # Test Mark No Match
        resp_nm = await ac.post(f"/api/v1/episodes/{ep_id}/mark-no-match", json={
            "source_name": "tvmaze"
        })
        assert resp_nm.status_code == 200
        data_nm = resp_nm.json()
        variations = data_nm["source_variations"]
        tvmaze_var = [v for v in variations if v["source_name"] == "tvmaze"]
        assert len(tvmaze_var) == 1
        assert tvmaze_var[0]["match_method"] == "NO_MATCH"
