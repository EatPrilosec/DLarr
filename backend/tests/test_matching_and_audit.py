import pytest
from unittest.mock import patch
from backend.app.core.database import AsyncSessionLocal
from backend.app.models.show import Show, Episode, EpisodeSourceMetadata
from backend.app.services.matching_engine import (
    normalize_title,
    token_sorted_title,
    extract_title_aliases,
    is_title_match,
    SourceIndex,
    MatchingEngine
)
from backend.app.services.ollama_client import OllamaClient
from backend.app.services.audit_engine import AuditEngine


def test_normalize_title():
    assert normalize_title("The Walking Dead: Season 1!") == "the walking dead season 1"
    assert normalize_title("Doctor Who (2005)") == "doctor who 2005"
    assert normalize_title(None) == ""


def test_extract_title_aliases():
    aliases = extract_title_aliases("Racing The Storm (American Airlines, Flight 1420)")
    assert "Racing The Storm (American Airlines, Flight 1420)" in aliases
    assert "Racing The Storm" in aliases
    assert "American Airlines, Flight 1420" in aliases

    match, method, conf = is_title_match("Racing The Storm (American Airlines, Flight 1420)", "Racing the Storm")
    assert match is True
    assert method == "EXACT_TITLE"


def test_token_sorted_and_fuzzy_title_match():
    # Reversed names matching
    match, method, conf = is_title_match("Opie and Flirt", "Flirt and Opie")
    assert match is True
    assert method == "EXACT_TITLE_REORDERED"

    match, method, conf = is_title_match("Boomer and Josh", "Josh and Boomer")
    assert match is True
    assert method == "EXACT_TITLE_REORDERED"

    # Slight spelling difference in names (e.g. Sueki vs Suecki)
    match, method, conf = is_title_match("Sueki and Coach", "Suecki and Coach")
    assert match is True
    assert method == "FUZZY_TITLE_MATCH"
    assert conf >= 0.88


@pytest.mark.asyncio
async def test_ollama_retry_fallback_execution():
    call_counts = {"primary": 0, "fallback": 0}

    async def mock_query(base_url, model, user_prompt, system_prompt=None, timeout=60.0):
        if model == "primary:8b":
            call_counts["primary"] += 1
            if call_counts["primary"] == 1:
                return ""  # Blank on try 1
            raise RuntimeError("Error on retry 2")
        elif model == "fallback:7b":
            call_counts["fallback"] += 1
            return "yes"  # Matches on fallback
        return ""

    with patch.object(OllamaClient, "query_model_text", side_effect=mock_query):
        res, model_used = await OllamaClient.query_with_retry_and_fallback(
            base_url="http://localhost:11434",
            primary_model="primary:8b",
            fallback_model="fallback:7b",
            user_prompt="test prompt"
        )
        assert res == "yes"
        assert model_used == "fallback:7b"
        assert call_counts["primary"] == 2
        assert call_counts["fallback"] == 1


@pytest.mark.asyncio
async def test_ai_search_candidates_parsing():
    async def mock_query(base_url, model, user_prompt, system_prompt=None, timeout=60.0):
        return "The matching episode is S01E02."

    ep = Episode(
        id=1,
        season_number=1,
        episode_number=1,
        title="Racing The Storm (American Airlines, Flight 1420)",
        overview="Flight 1420 crashes in storm",
        air_date="2003-09-03"
    )

    candidates = [
        {"season": 1, "episode": 1, "title": "Unlocking Disaster", "overview": "United 811", "air_date": "2003-09-03"},
        {"season": 1, "episode": 2, "title": "Racing the Storm", "overview": "Flight 1420", "air_date": "2003-09-10"}
    ]

    with patch.object(OllamaClient, "query_model_text", side_effect=mock_query):
        se_match, model_used = await MatchingEngine.ai_search_candidates(
            ollama_url="http://localhost:11434",
            primary_model="primary:8b",
            fallback_model="fallback:7b",
            show_title="Mayday",
            canonical_ep=ep,
            candidates=candidates,
            source_name="TVmaze"
        )
        assert se_match == (1, 2)


@pytest.mark.asyncio
async def test_audit_engine_execution():
    import random
    s_id = random.randint(100000, 999999)
    async with AsyncSessionLocal() as db:
        show = Show(
            sonarr_series_id=s_id,
            title="Test AI Show",
            year=2024,
            status="continuing"
        )
        db.add(show)
        await db.flush()

        ep = Episode(
            show_id=show.id,
            sonarr_episode_id=s_id * 10,
            season_number=1,
            episode_number=1,
            title="Pilot Episode",
            overview="The beginning of the story"
        )
        db.add(ep)
        await db.flush()

        meta = EpisodeSourceMetadata(
            episode_id=ep.id,
            show_id=show.id,
            source_name="tmdb",
            source_season_number=1,
            source_episode_number=1,
            title="Pilot Episode",
            overview="The story starts here."
        )
        db.add(meta)
        await db.commit()

        mock_audit_res = {
            "verdict": "PASSED",
            "findings": [
                {
                    "episode_id": ep.id,
                    "is_valid": True,
                    "confidence": 98,
                    "status": "EXACT_MATCH",
                    "notes": "Verified identical pilot mapping"
                }
            ]
        }

        with patch.object(OllamaClient, "generate_with_fallback", return_value=(mock_audit_res, "llama3.1:8b")):
            res = await AuditEngine.audit_show_consistency(
                db=db,
                show_id=show.id,
                config={"ollama_url": "http://localhost:11434"}
            )
            assert res["status"] == "PASSED"
            assert res["flagged_count"] == 0

        # Clean up
        await db.delete(show)
        await db.commit()
