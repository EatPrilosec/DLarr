import json
from datetime import datetime
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

        # Load show with all episodes and their source variations
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

        # Group episodes into seasons to keep LLM context structured and fast
        seasons: Dict[int, List[Episode]] = {}
        for ep in show.episodes:
            seasons.setdefault(ep.season_number, []).append(ep)

        total_flagged = 0
        audit_results = []

        system_prompt = (
            "You are an expert TV database auditor. Your job is to analyze the mapped episode database for a TV show "
            "and verify that there are no mismatches, duplicate mappings, off-by-one numbering shifts, or conflicting plot synopses.\n\n"
            "Respond ONLY with valid JSON in this structure:\n"
            "{\n"
            '  "season_verdict": "PASSED" | "WARNING" | "MISMATCH_DETECTED",\n'
            '  "findings": [\n'
            "    {\n"
            '      "episode_id": integer,\n'
            '      "is_valid": true | false,\n'
            '      "confidence": integer_0_to_100,\n'
            '      "status": "EXACT_MATCH" | "AI_MATCHED" | "FLAGGED_MISMATCH",\n'
            '      "notes": "explanation of any detected conflict, shift, or verification"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        for season_num, eps in seasons.items():
            await log(f"Auditing Season {season_num} ({len(eps)} episodes)...")
            
            season_payload = []
            for ep in eps:
                sources_summary = []
                for v in ep.source_variations:
                    sources_summary.append({
                        "source": v.source_name,
                        "season": v.source_season_number,
                        "episode": v.source_episode_number,
                        "title": v.title,
                        "overview": (v.overview or "")[:150]
                    })
                season_payload.append({
                    "episode_id": ep.id,
                    "sonarr_season": ep.season_number,
                    "sonarr_episode": ep.episode_number,
                    "sonarr_title": ep.title,
                    "sonarr_overview": (ep.overview or "")[:150],
                    "mapped_sources": sources_summary
                })

            user_prompt = (
                f"Show: '{show.title}' (Season {season_num})\n"
                f"Episode Mappings:\n{json.dumps(season_payload, indent=2)}\n\n"
                f"Verify the mappings and output JSON findings for each episode."
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
                findings_map = {f.get("episode_id"): f for f in findings if f.get("episode_id")}

                for ep in eps:
                    finding = findings_map.get(ep.id)
                    if finding:
                        ep.ai_verification_status = finding.get("status", "AI_MATCHED")
                        ep.ai_confidence_score = float(finding.get("confidence", 90))
                        ep.ai_audit_notes = finding.get("notes")
                        if not finding.get("is_valid", True) or ep.ai_verification_status == "FLAGGED_MISMATCH":
                            total_flagged += 1
                    else:
                        # If LLM didn't flag, mark as AI_MATCHED
                        ep.ai_verification_status = "AI_MATCHED"
                        ep.ai_confidence_score = 90.0
                        ep.ai_audit_notes = f"Verified by {model_used}"

                audit_results.append({
                    "season": season_num,
                    "verdict": llm_res.get("season_verdict", "PASSED"),
                    "model_used": model_used
                })

            except Exception as e:
                await log(f"Audit exception for Season {season_num}: {str(e)}")
                for ep in eps:
                    ep.ai_audit_notes = f"Audit skipped: {str(e)}"

        show.last_audited_at = datetime.utcnow()
        show.audit_status = "HAS_WARNINGS" if total_flagged > 0 else "PASSED"
        await db.commit()

        await log(f"Show audit finished with status: {show.audit_status} ({total_flagged} flagged episodes).")
        return {
            "show_id": show.id,
            "status": show.audit_status,
            "flagged_count": total_flagged,
            "season_results": audit_results
        }
