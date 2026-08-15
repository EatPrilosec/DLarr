import json
import re
from typing import Dict, Any, List, Optional, Callable, Awaitable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.models.show import Show, Episode, EpisodeSourceMetadata
from backend.app.models.job import Job
from backend.app.services.sonarr_client import SonarrClient
from backend.app.services.tmdb_client import TMDBClient
from backend.app.services.tvmaze_client import TVmazeClient
from backend.app.services.omdb_client import OMDbClient
from backend.app.services.subdl_client import SubDLClient
from backend.app.services.opensubtitles_client import OpenSubtitlesClient
from backend.app.services.transcript_service import TranscriptService
from backend.app.services.ollama_client import OllamaClient


def normalize_title(title: Optional[str]) -> str:
    if not title:
        return ""
    # Lowercase, strip punctuation and extra spaces
    t = title.lower()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


class MatchingEngine:
    @staticmethod
    async def match_source_candidates_with_llm(
        ollama_url: str,
        primary_model: str,
        fallback_model: Optional[str],
        canonical_ep: Episode,
        candidates: List[Dict[str, Any]],
        source_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Uses Ollama to find the best candidate episode matching the canonical Sonarr episode.
        """
        if not candidates or not ollama_url:
            return None

        # Build candidate list payload (limited to reasonable size)
        candidate_payload = []
        for c in candidates[:25]:  # Limit candidate pool
            candidate_payload.append({
                "candidate_id": str(c.get("id") or f"S{c.get('season')}E{c.get('episode')}"),
                "season": c.get("season"),
                "episode": c.get("episode"),
                "title": c.get("title"),
                "air_date": c.get("air_date"),
                "overview": (c.get("overview") or "")[:300]
            })

        system_prompt = (
            "You are an expert TV metadata matching engine. Your task is to match a target TV episode "
            "from Sonarr with its exact counterpart from an external source metadata database, even if episode "
            "numbers, season numbers, titles, or descriptions differ (due to differing broadcast orders, "
            "multi-part episodes, or translated titles).\n\n"
            "Respond ONLY with valid JSON in the following format:\n"
            "{\n"
            '  "matched_candidate_id": "candidate_id_string_or_null",\n'
            '  "is_match": true_or_false,\n'
            '  "confidence": integer_0_to_100,\n'
            '  "reasoning": "brief explanation of match or discrepancy"\n'
            "}"
        )

        user_prompt = (
            f"Target Episode (Sonarr):\n"
            f"- Season: {canonical_ep.season_number}\n"
            f"- Episode: {canonical_ep.episode_number}\n"
            f"- Title: {canonical_ep.title}\n"
            f"- Air Date: {canonical_ep.air_date}\n"
            f"- Overview: {canonical_ep.overview or 'N/A'}\n\n"
            f"Candidate Episodes from {source_name}:\n"
            f"{json.dumps(candidate_payload, indent=2)}\n\n"
            f"Identify the matching candidate or determine if no match exists."
        )

        try:
            result, model_used = await OllamaClient.generate_with_fallback(
                base_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            result["model_used"] = model_used
            return result
        except Exception as e:
            return {"error": str(e), "is_match": False, "confidence": 0}

    @classmethod
    async def process_show_ingestion(
        cls,
        db: AsyncSession,
        sonarr_series_id: int,
        config: Dict[str, Any],
        job: Optional[Job] = None,
        log_callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> Show:
        async def log(msg: str):
            if log_callback:
                await log_callback(msg)
            if job:
                job.logs = (job.logs or "") + f"\n{msg}"

        await log(f"Starting ingestion for Sonarr series ID {sonarr_series_id}...")

        # 1. Fetch from Sonarr
        sonarr_url = config.get("sonarr_url", "")
        sonarr_key = config.get("sonarr_api_key", "")
        if not sonarr_url or not sonarr_key:
            raise ValueError("Sonarr URL and API key are required.")

        series_data = await SonarrClient.get_series_detail(sonarr_url, sonarr_key, sonarr_series_id)
        episodes_data = await SonarrClient.get_episodes(sonarr_url, sonarr_key, sonarr_series_id)

        # 2. Create or update Show record
        stmt = select(Show).where(Show.sonarr_series_id == sonarr_series_id)
        result = await db.execute(stmt)
        show = result.scalars().first()

        title = series_data.get("title", "Unknown Title")
        clean_title = series_data.get("cleanTitle", normalize_title(title))
        
        poster_url = None
        for img in series_data.get("images", []):
            if img.get("coverType") == "poster":
                poster_url = img.get("remoteUrl") or img.get("url")

        if not show:
            show = Show(
                sonarr_series_id=sonarr_series_id,
                title=title,
                clean_title=clean_title,
                sort_title=series_data.get("sortTitle"),
                year=series_data.get("year"),
                status=series_data.get("status"),
                overview=series_data.get("overview"),
                poster_url=poster_url,
                tvdb_id=series_data.get("tvdbId"),
                tmdb_id=series_data.get("tmdbId"),
                imdb_id=series_data.get("imdbId"),
                path=series_data.get("path"),
                monitored=series_data.get("monitored", True)
            )
            db.add(show)
            await db.flush()
        else:
            show.title = title
            show.overview = series_data.get("overview")
            show.poster_url = poster_url
            show.tvdb_id = series_data.get("tvdbId") or show.tvdb_id
            show.imdb_id = series_data.get("imdbId") or show.imdb_id
            show.tmdb_id = series_data.get("tmdbId") or show.tmdb_id
            await db.flush()

        await log(f"Show initialized: '{show.title}' (TVDB: {show.tvdb_id}, IMDb: {show.imdb_id}, TMDB: {show.tmdb_id})")

        # 3. Create or update canonical Episodes
        episodes_map: Dict[int, Episode] = {}
        for ep_data in episodes_data:
            s_ep_id = ep_data.get("id")
            stmt_ep = select(Episode).where(Episode.sonarr_episode_id == s_ep_id)
            res_ep = await db.execute(stmt_ep)
            ep = res_ep.scalars().first()
            if not ep:
                ep = Episode(
                    show_id=show.id,
                    sonarr_episode_id=s_ep_id,
                    season_number=ep_data.get("seasonNumber", 1),
                    episode_number=ep_data.get("episodeNumber", 1),
                    absolute_episode_number=ep_data.get("absoluteEpisodeNumber"),
                    title=ep_data.get("title", f"Episode {ep_data.get('episodeNumber')}"),
                    overview=ep_data.get("overview"),
                    air_date=ep_data.get("airDate"),
                    has_file=ep_data.get("hasFile", False),
                    monitored=ep_data.get("monitored", True),
                    ai_verification_status="PENDING"
                )
                db.add(ep)
                await db.flush()
            episodes_map[ep.id] = ep

            # Save Sonarr variation entry in episode_source_metadata if not exists
            stmt_meta = select(EpisodeSourceMetadata).where(
                EpisodeSourceMetadata.episode_id == ep.id,
                EpisodeSourceMetadata.source_name == "sonarr"
            )
            res_meta = await db.execute(stmt_meta)
            if not res_meta.scalars().first():
                sonarr_meta = EpisodeSourceMetadata(
                    episode_id=ep.id,
                    show_id=show.id,
                    source_name="sonarr",
                    source_show_id=str(sonarr_series_id),
                    source_episode_id=str(s_ep_id),
                    source_season_number=ep.season_number,
                    source_episode_number=ep.episode_number,
                    source_absolute_number=ep.absolute_episode_number,
                    title=ep.title,
                    overview=ep.overview,
                    air_date=ep.air_date,
                    match_method="EXACT_TITLE",
                    match_confidence=1.0,
                    raw_metadata=json.dumps(ep_data)
                )
                db.add(sonarr_meta)

        await db.commit()
        await log(f"Populated {len(episodes_map)} canonical Sonarr episodes.")

        # 4. Fetch External Metadata from Providers
        # A. TMDB
        tmdb_key = config.get("tmdb_api_key")
        tmdb_episodes_by_season: Dict[int, List[Dict[str, Any]]] = {}
        if tmdb_key:
            try:
                tmdb_id = show.tmdb_id
                if not tmdb_id and show.tvdb_id:
                    tmdb_id = await TMDBClient.find_by_external_id(tmdb_key, str(show.tvdb_id), "tvdb_id")
                if not tmdb_id and show.imdb_id:
                    tmdb_id = await TMDBClient.find_by_external_id(tmdb_key, show.imdb_id, "imdb_id")
                if not tmdb_id:
                    search_res = await TMDBClient.search_tv(tmdb_key, show.title, show.year)
                    if search_res:
                        tmdb_id = search_res[0].get("id")
                
                if tmdb_id:
                    show.tmdb_id = tmdb_id
                    await log(f"Fetching TMDB data for ID {tmdb_id}...")
                    seasons = set(ep.season_number for ep in episodes_map.values())
                    for s in seasons:
                        eps = await TMDBClient.get_season_episodes(tmdb_key, tmdb_id, s)
                        tmdb_episodes_by_season[s] = eps
                    await log(f"Retrieved TMDB metadata for {len(tmdb_episodes_by_season)} seasons.")
            except Exception as e:
                await log(f"TMDB fetch error: {str(e)}")

        # B. TVmaze
        tvmaze_episodes: List[Dict[str, Any]] = []
        try:
            tvmaze_show = await TVmazeClient.lookup_show(tvdb_id=show.tvdb_id, imdb_id=show.imdb_id, title=show.title)
            if tvmaze_show and tvmaze_show.get("id"):
                show.tvmaze_id = tvmaze_show.get("id")
                tvmaze_episodes = await TVmazeClient.get_episodes(show.tvmaze_id)
                await log(f"Retrieved {len(tvmaze_episodes)} episodes from TVmaze.")
        except Exception as e:
            await log(f"TVmaze fetch error: {str(e)}")

        # C. OMDb
        omdb_key = config.get("omdb_api_key")
        omdb_episodes_by_season: Dict[int, List[Dict[str, Any]]] = {}
        if omdb_key and show.imdb_id:
            try:
                seasons = set(ep.season_number for ep in episodes_map.values() if ep.season_number > 0)
                for s in seasons:
                    omdb_eps = await OMDbClient.get_season_episodes(omdb_key, show.imdb_id, s)
                    omdb_episodes_by_season[s] = omdb_eps
                await log(f"Retrieved OMDb metadata for {len(omdb_episodes_by_season)} seasons.")
            except Exception as e:
                await log(f"OMDb fetch error: {str(e)}")

        # D. Subtitles / Transcripts (SubDL)
        subdl_key = config.get("subdl_api_key")

        # 5. Matching Loop for each Episode across each Source
        ollama_url = config.get("ollama_url", "http://localhost:11434")
        primary_model = config.get("ollama_primary_model", "llama3.1:8b")
        fallback_model = config.get("ollama_fallback_model", "mistral:7b")

        total_eps = len(episodes_map)
        current_idx = 0

        for ep_id, canonical_ep in episodes_map.items():
            current_idx += 1
            if job:
                job.progress = round((current_idx / total_eps) * 80.0, 1)
                job.message = f"Matching episode {current_idx}/{total_eps}: S{canonical_ep.season_number}E{canonical_ep.episode_number}"

            clean_canonical = normalize_title(canonical_ep.title)

            # Match TMDB
            tmdb_candidates = tmdb_episodes_by_season.get(canonical_ep.season_number, [])
            matched_tmdb = None
            # Exact title or episode number match
            for t_ep in tmdb_candidates:
                if normalize_title(t_ep.get("name")) == clean_canonical or t_ep.get("episode_number") == canonical_ep.episode_number:
                    matched_tmdb = {
                        "id": str(t_ep.get("id")),
                        "season": t_ep.get("season_number"),
                        "episode": t_ep.get("episode_number"),
                        "title": t_ep.get("name"),
                        "overview": t_ep.get("overview"),
                        "air_date": t_ep.get("air_date"),
                        "method": "EXACT_TITLE",
                        "confidence": 1.0,
                        "raw": t_ep
                    }
                    break

            if not matched_tmdb and tmdb_candidates and ollama_url:
                # LLM Match TMDB
                cand_list = [{"id": str(c.get("id")), "season": c.get("season_number"), "episode": c.get("episode_number"), "title": c.get("name"), "overview": c.get("overview"), "air_date": c.get("air_date")} for c in tmdb_candidates]
                llm_res = await cls.match_source_candidates_with_llm(ollama_url, primary_model, fallback_model, canonical_ep, cand_list, "TMDB")
                if llm_res and llm_res.get("is_match") and llm_res.get("matched_candidate_id"):
                    target_id = str(llm_res["matched_candidate_id"])
                    chosen = next((c for c in tmdb_candidates if str(c.get("id")) == target_id), None)
                    if chosen:
                        matched_tmdb = {
                            "id": str(chosen.get("id")),
                            "season": chosen.get("season_number"),
                            "episode": chosen.get("episode_number"),
                            "title": chosen.get("name"),
                            "overview": chosen.get("overview"),
                            "air_date": chosen.get("air_date"),
                            "method": "AI_LLM_CONFIRMED",
                            "confidence": (llm_res.get("confidence", 80)) / 100.0,
                            "raw": chosen
                        }

            if matched_tmdb:
                meta = EpisodeSourceMetadata(
                    episode_id=canonical_ep.id,
                    show_id=show.id,
                    source_name="tmdb",
                    source_show_id=str(show.tmdb_id),
                    source_episode_id=matched_tmdb["id"],
                    source_season_number=matched_tmdb["season"],
                    source_episode_number=matched_tmdb["episode"],
                    title=matched_tmdb["title"],
                    overview=matched_tmdb["overview"],
                    air_date=matched_tmdb["air_date"],
                    match_method=matched_tmdb["method"],
                    match_confidence=matched_tmdb["confidence"],
                    raw_metadata=json.dumps(matched_tmdb["raw"])
                )
                db.add(meta)

            # Match TVmaze
            matched_tvmaze = None
            for tv_ep in tvmaze_episodes:
                if tv_ep.get("season") == canonical_ep.season_number and (normalize_title(tv_ep.get("name")) == clean_canonical or tv_ep.get("number") == canonical_ep.episode_number):
                    matched_tvmaze = {
                        "id": str(tv_ep.get("id")),
                        "season": tv_ep.get("season"),
                        "episode": tv_ep.get("number"),
                        "title": tv_ep.get("name"),
                        "overview": re.sub(r"<[^>]+>", "", tv_ep.get("summary") or ""),
                        "air_date": tv_ep.get("airdate"),
                        "method": "EXACT_TITLE",
                        "confidence": 1.0,
                        "raw": tv_ep
                    }
                    break

            if not matched_tvmaze and tvmaze_episodes and ollama_url:
                cand_list = [{"id": str(c.get("id")), "season": c.get("season"), "episode": c.get("number"), "title": c.get("name"), "overview": c.get("summary"), "air_date": c.get("airdate")} for c in tvmaze_episodes if c.get("season") == canonical_ep.season_number]
                if not cand_list:
                    cand_list = [{"id": str(c.get("id")), "season": c.get("season"), "episode": c.get("number"), "title": c.get("name"), "overview": c.get("summary"), "air_date": c.get("airdate")} for c in tvmaze_episodes]
                llm_res = await cls.match_source_candidates_with_llm(ollama_url, primary_model, fallback_model, canonical_ep, cand_list, "TVmaze")
                if llm_res and llm_res.get("is_match") and llm_res.get("matched_candidate_id"):
                    target_id = str(llm_res["matched_candidate_id"])
                    chosen = next((c for c in tvmaze_episodes if str(c.get("id")) == target_id), None)
                    if chosen:
                        matched_tvmaze = {
                            "id": str(chosen.get("id")),
                            "season": chosen.get("season"),
                            "episode": chosen.get("number"),
                            "title": chosen.get("name"),
                            "overview": re.sub(r"<[^>]+>", "", chosen.get("summary") or ""),
                            "air_date": chosen.get("airdate"),
                            "method": "AI_LLM_CONFIRMED",
                            "confidence": (llm_res.get("confidence", 80)) / 100.0,
                            "raw": chosen
                        }

            if matched_tvmaze:
                meta = EpisodeSourceMetadata(
                    episode_id=canonical_ep.id,
                    show_id=show.id,
                    source_name="tvmaze",
                    source_show_id=str(show.tvmaze_id),
                    source_episode_id=matched_tvmaze["id"],
                    source_season_number=matched_tvmaze["season"],
                    source_episode_number=matched_tvmaze["episode"],
                    title=matched_tvmaze["title"],
                    overview=matched_tvmaze["overview"],
                    air_date=matched_tvmaze["air_date"],
                    match_method=matched_tvmaze["method"],
                    match_confidence=matched_tvmaze["confidence"],
                    raw_metadata=json.dumps(matched_tvmaze["raw"])
                )
                db.add(meta)

            # Match OMDb
            omdb_candidates = omdb_episodes_by_season.get(canonical_ep.season_number, [])
            matched_omdb = None
            for o_ep in omdb_candidates:
                ep_num = int(o_ep.get("Episode", 0)) if str(o_ep.get("Episode", "")).isdigit() else None
                if normalize_title(o_ep.get("Title")) == clean_canonical or ep_num == canonical_ep.episode_number:
                    matched_omdb = {
                        "id": o_ep.get("imdbID") or f"omdb_S{canonical_ep.season_number}E{ep_num}",
                        "season": canonical_ep.season_number,
                        "episode": ep_num,
                        "title": o_ep.get("Title"),
                        "overview": o_ep.get("Plot"),
                        "air_date": o_ep.get("Released"),
                        "method": "EXACT_TITLE",
                        "confidence": 1.0,
                        "raw": o_ep
                    }
                    break

            if matched_omdb:
                meta = EpisodeSourceMetadata(
                    episode_id=canonical_ep.id,
                    show_id=show.id,
                    source_name="omdb",
                    source_show_id=show.imdb_id,
                    source_episode_id=matched_omdb["id"],
                    source_season_number=matched_omdb["season"],
                    source_episode_number=matched_omdb["episode"],
                    title=matched_omdb["title"],
                    overview=matched_omdb["overview"],
                    air_date=matched_omdb["air_date"],
                    match_method=matched_omdb["method"],
                    match_confidence=matched_omdb["confidence"],
                    raw_metadata=json.dumps(matched_omdb["raw"])
                )
                db.add(meta)

            # SubDL Transcript check if key provided
            if subdl_key:
                try:
                    subs = await SubDLClient.search_subtitles(
                        api_key=subdl_key,
                        imdb_id=show.imdb_id,
                        tmdb_id=show.tmdb_id,
                        season_number=canonical_ep.season_number,
                        episode_number=canonical_ep.episode_number
                    )
                    if subs:
                        sub_meta = EpisodeSourceMetadata(
                            episode_id=canonical_ep.id,
                            show_id=show.id,
                            source_name="subdl",
                            source_show_id=show.imdb_id,
                            source_season_number=canonical_ep.season_number,
                            source_episode_number=canonical_ep.episode_number,
                            title=f"Subtitles S{canonical_ep.season_number}E{canonical_ep.episode_number}",
                            has_transcript=True,
                            transcript_preview="Subtitle release indexed from SubDL.",
                            match_method="EXACT_TITLE",
                            match_confidence=1.0,
                            raw_metadata=json.dumps(subs[0])
                        )
                        db.add(sub_meta)
                except Exception:
                    pass

        await db.commit()
        await log("Completed multi-source episode matching pass.")
        return show
