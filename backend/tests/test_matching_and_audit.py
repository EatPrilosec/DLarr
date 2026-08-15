import pytest
from unittest.mock import patch
from backend.app.core.database import AsyncSessionLocal
from backend.app.models.show import Show, Episode, EpisodeSourceMetadata
from backend.app.services.matching_engine import normalize_title
from backend.app.services.ollama_client import OllamaClient
from backend.app.services.audit_engine import AuditEngine


def test_normalize_title():
    assert normalize_title("The Walking Dead: Season 1!") == "the walking dead season 1"
    assert normalize_title("Doctor Who (2005)") == "doctor who 2005"
    assert normalize_title(None) == ""


@pytest.mark.asyncio
async def test_ollama_fallback_routing():
    async def mock_query(base_url, model, system_prompt, user_prompt, timeout=60.0):
        if model == "primary:8b":
            raise TimeoutError("Primary model timed out")
        elif model == "fallback:7b":
            return {"matched_candidate_id": "cand_1", "is_match": True, "confidence": 95}
        raise ValueError(f"Unexpected model: {model}")

    with patch.object(OllamaClient, "query_model_structured", side_effect=mock_query):
        result, model_used = await OllamaClient.generate_with_fallback(
            base_url="http://localhost:11434",
            primary_model="primary:8b",
            fallback_model="fallback:7b",
            system_prompt="Test system",
            user_prompt="Test user"
        )
        assert result["matched_candidate_id"] == "cand_1"
        assert model_used == "fallback:7b"


@pytest.mark.asyncio
async def test_audit_engine_execution():
    async with AsyncSessionLocal() as db:
        show = Show(
            sonarr_series_id=999,
            title="Test AI Show",
            year=2024,
            status="continuing"
        )
        db.add(show)
        await db.flush()

        ep = Episode(
            show_id=show.id,
            sonarr_episode_id=99901,
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
            "season_verdict": "PASSED",
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
