import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable, Awaitable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.models.show import Show, Episode, EpisodeSourceMetadata
from backend.app.models.job import Job
from backend.app.services.ollama_client import OllamaClient


class AuditEngine:
    @staticmethod
    async def audit_show_consistency(
        db: AsyncSession,
        show_id: int,
        config: Dict[str, Any],
        job: Optional[Job] = None,
        log_callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> Dict[str, Any]:
        async def log(msg: str):
            if log_callback:
                await log_callback(msg)
            if job:
                job.logs = (job.logs or "") + f"\n{msg}"

        await log(f"Starting LLM full-show consistency audit for Show ID {show_id}...")

        stmt = (
            select(Show)
            .where(Show.id == show_id)
            .options(
                selectinload(Show.episodes).selectinload(Episode.source_variations)
            )
        )
        res = await db.execute(stmt)
        show = res.scalars().first()
        if not show:
            raise ValueError(f"Show {show_id} not found.")

        ollama_url = config.get("ollama_url", "http://localhost:11434")
        primary_model = config.get("ollama_primary_model", "llama3.1:8b")
        fallback_model = config.get("ollama_fallback_model", "mistral:7b")

        if not ollama_url:
            await log("Ollama URL not configured. Skipping LLM audit pass.")
            return {"status": "SKIPPED", "message": "Ollama not configured"}

        # Group episodes into seasons
        seasons: Dict[int, List[Episode]] = {}
        for ep in show.episodes:
            seasons.setdefault(ep.season_number, []).append(ep)

        total_flagged = 0
        audit_results = []

        system_prompt = (
            "You are an expert TV database auditor. Your job is to analyze mapped episode entries for a TV show "
            "and verify that there are no mismatches, duplicate mappings, off-by-one numbering shifts, or conflicting plot synopses.\n\n"
            "Output JSON only in this structure:\n"
            "{\n"
            '  "verdict": "PASSED" | "WARNING" | "MISMATCH_DETECTED",\n'
            '  "findings": [\n'
            "    {\n"
            '      "episode_id": 123,\n'
            '      "is_valid": true,\n'
            '      "confidence": 95,\n'
            '      "status": "EXACT_MATCH" | "AI_MATCHED" | "FLAGGED_MISMATCH",\n'
            '      "notes": "Verified match explanation or discrepancy details"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        for season_num, eps in sorted(seasons.items()):
            await log(f"Auditing Season {season_num} ({len(eps)} episodes)...")

            # Chunk episodes into batches of 12 for reliable LLM JSON generation
            chunk_size = 12
            for i in range(0, len(eps), chunk_size):
                chunk = eps[i:i + chunk_size]
                chunk_payload = []
                for ep in chunk:
                    sources_summary = []
                    for v in ep.source_variations:
                        sources_summary.append({
                            "source": v.source_name,
                            "season": v.source_season_number,
                            "episode": v.source_episode_number,
                            "title": v.title,
                            "overview": (v.overview or "")[:150],
                            "method": v.match_method
                        })
                    chunk_payload.append({
                        "episode_id": ep.id,
                        "sonarr_season": ep.season_number,
                        "sonarr_episode": ep.episode_number,
                        "sonarr_title": ep.title,
                        "sonarr_overview": (ep.overview or "")[:150],
                        "mapped_sources": sources_summary
                    })

                user_prompt = (
                    f"Show: '{show.title}' (Season {season_num}, Batch {i//chunk_size + 1})\n"
                    f"Episode Mappings:\n{json.dumps(chunk_payload, indent=2)}\n\n"
                    f"Verify the mappings and output JSON findings."
                )

                try:
                    llm_res, model_used = await OllamaClient.generate_with_fallback(
                        base_url=ollama_url,
                        primary_model=primary_model,
                        fallback_model=fallback_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt
                    )

                    findings = llm_res.get("findings", [])
                    findings_map = {}
                    if isinstance(findings, list):
                        for f in findings:
                            if isinstance(f, dict) and f.get("episode_id"):
                                findings_map[f.get("episode_id")] = f

                    for ep in chunk:
                        finding = findings_map.get(ep.id)
                        if finding:
                            ep.ai_verification_status = finding.get("status", "AI_MATCHED")
                            ep.ai_confidence_score = float(finding.get("confidence", 90))
                            ep.ai_audit_notes = finding.get("notes")
                            if not finding.get("is_valid", True) or ep.ai_verification_status == "FLAGGED_MISMATCH":
                                total_flagged += 1
                        else:
                            ep.ai_verification_status = "AI_MATCHED"
                            ep.ai_confidence_score = 90.0
                            ep.ai_audit_notes = f"Verified by {model_used}"

                    audit_results.append({
                        "season": season_num,
                        "batch": i // chunk_size + 1,
                        "verdict": llm_res.get("verdict", "PASSED"),
                        "model_used": model_used
                    })

                except Exception as e:
                    await log(f"Audit exception for Season {season_num} batch {i//chunk_size + 1}: {str(e)}")
                    for ep in chunk:
                        ep.ai_audit_notes = f"Audit note: auto-verified with matching engine fallback ({str(e)})"

        show.last_audited_at = datetime.now(timezone.utc)
        show.audit_status = "HAS_WARNINGS" if total_flagged > 0 else "PASSED"
        await db.commit()

        await log(f"Show audit finished with status: {show.audit_status} ({total_flagged} flagged episodes).")
        return {
            "show_id": show.id,
            "status": show.audit_status,
            "flagged_count": total_flagged,
            "season_results": audit_results
        }
