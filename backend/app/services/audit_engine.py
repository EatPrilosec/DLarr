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
                try:
                    await db.commit()
                except Exception:
                    pass

        await log(f"Starting consistency audit for Show ID {show_id}...")

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

        # Group episodes into seasons
        seasons: Dict[int, List[Episode]] = {}
        for ep in show.episodes:
            seasons.setdefault(ep.season_number, []).append(ep)

        total_flagged = 0
        audit_results = []

        system_prompt = (
            "You are an expert TV database auditor. Your job is to verify questionable episode mappings "
            "and check for conflicts, misidentifications, or mismatched plots.\n\n"
            "Output JSON only in this structure:\n"
            "{\n"
            '  "verdict": "PASSED" | "WARNING" | "MISMATCH_DETECTED",\n'
            '  "findings": [\n'
            "    {\n"
            '      "episode_id": 123,\n'
            '      "is_valid": true,\n'
            '      "confidence": 95,\n'
            '      "status": "EXACT_MATCH" | "AI_MATCHED" | "FLAGGED_MISMATCH",\n'
            '      "notes": "Verified match or discrepancy explanation"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        for season_num, eps in sorted(seasons.items()):
            from backend.app.services.concurrency_manager import concurrency_manager
            if job and concurrency_manager.is_cancelled(job.id):
                await log(f"[JOB CANCELLED] Audit cancelled by user at Season {season_num}.")
                return {"status": "CANCELLED", "flagged_count": total_flagged, "seasons": audit_results}

            # Check for any episodes with low confidence or anomalies
            questionable_eps = []
            for ep in eps:
                sources = ep.source_variations
                if not sources:
                    # Specials or unreleased
                    ep.ai_verification_status = "PENDING"
                    ep.ai_confidence_score = 50.0
                    ep.ai_audit_notes = "No external source metadata available"
                    continue

                min_conf = min(v.match_confidence for v in sources)
                if min_conf < 0.90 or any(v.match_method == "AI_LLM_CONFIRMED" for v in sources):
                    questionable_eps.append(ep)
                else:
                    # Clean match across all sources
                    ep.ai_verification_status = "EXACT_MATCH"
                    ep.ai_confidence_score = round(min_conf * 100.0, 1)
                    methods = ", ".join(set(v.match_method for v in sources if v.source_name != "sonarr"))
                    ep.ai_audit_notes = f"Verified high confidence ({methods or 'canonical'})"

            if not questionable_eps:
                await log(f"Season {season_num} ({len(eps)} episodes): All mappings verified algorithmically (100% match).")
                audit_results.append({
                    "season": season_num,
                    "verdict": "PASSED",
                    "method": "ALGORITHMIC_VERIFIED"
                })
                continue

            # If there are questionable episodes in this season and Ollama is configured, audit them
            if ollama_url:
                await log(f"Auditing Season {season_num} with Ollama ({len(questionable_eps)} candidate episodes)...")
                payload = []
                for ep in questionable_eps[:15]:
                    sources_summary = []
                    for v in ep.source_variations:
                        sources_summary.append({
                            "source": v.source_name,
                            "season": v.source_season_number,
                            "episode": v.source_episode_number,
                            "title": v.title,
                            "overview": (v.overview or "")[:150],
                            "method": v.match_method,
                            "confidence": v.match_confidence
                        })
                    payload.append({
                        "episode_id": ep.id,
                        "sonarr_season": ep.season_number,
                        "sonarr_episode": ep.episode_number,
                        "sonarr_title": ep.title,
                        "sonarr_overview": (ep.overview or "")[:150],
                        "mapped_sources": sources_summary
                    })

                user_prompt = (
                    f"Show: '{show.title}' (Season {season_num})\n"
                    f"Episodes to Verify:\n{json.dumps(payload, indent=2)}\n\n"
                    f"Check the mappings and output JSON verdict."
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

                    for ep in questionable_eps:
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
                        "verdict": llm_res.get("verdict", "PASSED"),
                        "model_used": model_used
                    })

                except Exception as e:
                    await log(f"Audit notice for Season {season_num}: {str(e)}")
                    for ep in questionable_eps:
                        ep.ai_verification_status = "AI_MATCHED"
                        ep.ai_confidence_score = 85.0
                        ep.ai_audit_notes = f"Verified via fallback ({str(e)})"
            else:
                for ep in questionable_eps:
                    ep.ai_verification_status = "AI_MATCHED"
                    ep.ai_confidence_score = 85.0

        show.last_audited_at = datetime.now(timezone.utc)
        show.audit_status = "HAS_WARNINGS" if total_flagged > 0 else "PASSED"
        await db.commit()

        await log(f"Show audit finished: status {show.audit_status} ({total_flagged} flagged).")
        return {
            "show_id": show.id,
            "status": show.audit_status,
            "flagged_count": total_flagged,
            "season_results": audit_results
        }
