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

    with patch("backend.app.services.ollama_client.OllamaClient.query_model_text", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = "yes"

        confirmed_ids, model_used = await MatchingEngine.ai_batch_confirm_matches(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model="fallback",
            show_title="Test Show",
            source_name="TMDB",
            batch_pairs=batch_pairs
        )
        assert confirmed_ids == {1, 2}
        assert model_used == "primary"


@pytest.mark.asyncio
async def test_step1_batch_confirm_partial_with_fallback():
    batch_pairs = [
        (Episode(id=10, season_number=1, episode_number=1, title="Ep 1"), {"season": 1, "episode": 1, "title": "Ep 1"}),
        (Episode(id=20, season_number=1, episode_number=2, title="Ep 2"), {"season": 1, "episode": 2, "title": "Shifted Story"}),
    ]

    with patch("backend.app.services.ollama_client.OllamaClient.query_model_text", new_callable=AsyncMock) as mock_query:
        # Primary rejects S01E02. Fallback is called on the remaining unconfirmed pair and confirms it ("yes").
        mock_query.side_effect = [
            "S01E02",  # Primary response (only confirms ep 10)
            "yes"      # Fallback response for remaining ep 20
        ]

        confirmed_ids, model_used = await MatchingEngine.ai_batch_confirm_matches(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model="fallback",
            show_title="Test Show",
            source_name="TMDB",
            batch_pairs=batch_pairs
        )
        # Both confirmed thanks to fallback
        assert confirmed_ids == {10, 20}
        assert "fallback" in model_used


@pytest.mark.asyncio
async def test_step2_ai_search_candidates():
    sonarr_ep = Episode(id=1, season_number=1, episode_number=5, title="Lost Flight")
    candidates = [
        {"season": 2, "episode": 3, "title": "Lost Flight", "overview": "A plane goes missing"},
    ]

    async def mock_query(base_url, model, user_prompt, system_prompt=None, timeout=None):
        if model == "primary":
            return "S02E03"
        return ""

    with patch("backend.app.services.ollama_client.OllamaClient.query_model_text", side_effect=mock_query):
        res, model_used = await MatchingEngine.ai_search_candidates(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model=["fb_1", "fb_2"],
            show_title="Test Show",
            canonical_ep=sonarr_ep,
            candidates=candidates,
            source_name="TMDB"
        )
        assert res == (2, 3)
        assert model_used == "primary"

    # Test fallback search when primary returns invalid/NONE
    async def mock_query_fallback(base_url, model, user_prompt, system_prompt=None, timeout=None):
        if model == "primary":
            return "NONE"
        elif model == "fb_1":
            return "no match found"
        elif model == "fb_2":
            return "match is S02E03"
        return ""

    with patch("backend.app.services.ollama_client.OllamaClient.query_model_text", side_effect=mock_query_fallback):
        res_fb, model_used_fb = await MatchingEngine.ai_search_candidates(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model=["fb_1", "fb_2"],
            show_title="Test Show",
            canonical_ep=sonarr_ep,
            candidates=candidates,
            source_name="TMDB"
        )
        assert res_fb == (2, 3)
        assert model_used_fb == "primary+fb_1+fb_2"


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


@pytest.mark.asyncio
async def test_batch_cascades_through_multiple_fallbacks():
    ep1 = Episode(id=101, season_number=1, episode_number=1, title="Alpha")
    ep2 = Episode(id=102, season_number=1, episode_number=2, title="Beta")
    ep3 = Episode(id=103, season_number=1, episode_number=3, title="Gamma")

    batch_pairs = [
        (ep1, {"season": 1, "episode": 1, "title": "Alpha"}),
        (ep2, {"season": 1, "episode": 2, "title": "Beta"}),
        (ep3, {"season": 1, "episode": 3, "title": "Gamma"}),
    ]

    # Primary model rejects S01E02 and S01E03 (confirms only ep1)
    # Fallback 1 confirms S01E02 (rejects S01E03)
    # Fallback 2 confirms S01E03
    async def mock_query(base_url, model, user_prompt, system_prompt=None, timeout=None):
        if model == "primary":
            return "S01E02, S01E03"  # failed list
        elif model == "fb_1":
            return "S01E03"          # failed list
        elif model == "fb_2":
            return "yes"             # confirms ep3
        return ""

    with patch("backend.app.services.ollama_client.OllamaClient.query_model_text", side_effect=mock_query):
        confirmed_ids, model_used = await MatchingEngine.ai_batch_confirm_matches(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model=["fb_1", "fb_2"],
            show_title="Test Cascade Show",
            source_name="TMDB",
            batch_pairs=batch_pairs
        )
        assert confirmed_ids == {101, 102, 103}
        assert model_used == "primary+fb_1+fb_2"


@pytest.mark.asyncio
async def test_json_string_fallback_models_parsed():
    # Test that when config dictionary contains JSON string for fallback_models, it cascades through both
    ep1 = Episode(id=201, season_number=1, episode_number=1, title="One")
    ep2 = Episode(id=202, season_number=1, episode_number=2, title="Two")

    batch_pairs = [
        (ep1, {"season": 1, "episode": 1, "title": "One"}),
        (ep2, {"season": 1, "episode": 2, "title": "Two"}),
    ]

    async def mock_query(base_url, model, user_prompt, system_prompt=None, timeout=None):
        if model == "primary":
            return "S01E02"  # failed ep2
        elif model == "fb_alpha":
            return "S01E02"  # also failed ep2
        elif model == "fb_beta":
            return "yes"     # confirmed ep2
        return ""

    raw_json_fallbacks = '["fb_alpha", "fb_beta"]'
    parsed_fallbacks = json.loads(raw_json_fallbacks)

    with patch("backend.app.services.ollama_client.OllamaClient.query_model_text", side_effect=mock_query):
        confirmed_ids, model_used = await MatchingEngine.ai_batch_confirm_matches(
            ollama_url="http://mock:11434",
            primary_model="primary",
            fallback_model=parsed_fallbacks,
            show_title="Test Show",
            source_name="TMDB",
            batch_pairs=batch_pairs
        )
        assert confirmed_ids == {201, 202}
        assert model_used == "primary+fb_alpha+fb_beta"
