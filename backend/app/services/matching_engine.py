import json
import re
import difflib
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable
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
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def token_sorted_title(title: Optional[str]) -> str:
    if not title:
        return ""
    t = normalize_title(title)
    words = [w for w in t.split() if w not in ('and', 'the', 'a', 'an', 'part', 'pt', 'vol', 'volume')]
    words.sort()
    return " ".join(words)


def is_title_match(t1: Optional[str], t2: Optional[str]) -> Tuple[bool, str, float]:
    """
    Returns (is_match, match_method, confidence)
    """
    if not t1 or not t2:
        return False, "NONE", 0.0

    n1 = normalize_title(t1)
    n2 = normalize_title(t2)
    if n1 == n2:
        return True, "EXACT_TITLE", 1.0

    s1 = token_sorted_title(t1)
    s2 = token_sorted_title(t2)
    if s1 and s2 and s1 == s2:
        return True, "EXACT_TITLE_REORDERED", 0.98

    # Fuzzy similarity check
    sim = difflib.SequenceMatcher(None, n1, n2).ratio()
    if sim >= 0.88:
        return True, "FUZZY_TITLE_MATCH", round(sim, 2)

    # Token overlap check (for titles like "Sueki and Coach" vs "Suecki and Coach")
    s_sim = difflib.SequenceMatcher(None, s1, s2).ratio()
    if s_sim >= 0.88:
        return True, "FUZZY_TITLE_MATCH", round(s_sim, 2)

    return False, "NONE", 0.0


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

        # Build candidate list payload
        candidate_payload = []
        for c in candidates[:20]:
            candidate_payload.append({
                "candidate_id": str(c.get("id")),
                "season": c.get("season"),
                "episode": c.get("episode"),
                "title": c.get("title"),
                "air_date": c.get("air_date"),
                "overview": (c.get("overview") or "")[:200]
            })

        system_prompt = (
            "You are an expert TV metadata matching engine. Your task is to match a target TV episode "
            "from Sonarr with its exact counterpart from an external source metadata database, even if episode "
            "numbers, season numbers, titles, or descriptions differ (due to broadcast orders, "
            "multi-part episodes, or translated titles).\n\n"
            "Output JSON only in this format:\n"
            "{\n"
            '  "matched_candidate_id": "candidate_id_string_or_null",\n'
            '  "is_match": true_or_false,\n'
            '  "confidence": integer_0_to_100,\n'
            '  "reasoning": "brief explanation"\n'
            "}"
        )

        user_prompt = (
            f"Target Episode (Sonarr):\n"
            f"- Season: {canonical_ep.season_number}\n"
            f"- Episode: {canonical_ep.episode_number}\n"
            f"- Title: {canonical_ep.title}\n"
            f"- Air Date: {canonical_ep.air_date or 'N/A'}\n"
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
    async def match_episode_against_source(
        cls,
        canonical_ep: Episode,
        source_episodes_all: List[Dict[str, Any]],
        source_name: str,
        ollama_url: str,
        primary_model: str,
        fallback_model: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """
        Multi-tier match for a single episode against all available source episodes:
        1. Exact or reordered/fuzzy title match in same season.
        2. Exact or reordered/fuzzy title match across ANY season of the source.
        3. LLM semantic candidate search if no title match found.
        """
        if not source_episodes_all:
            return None

        # 1. Check same season candidates first
        same_season_candidates = [
            c for c in source_episodes_all if c.get("season") == canonical_ep.season_number
        ]
        
        for cand in same_season_candidates:
            matched, method, conf = is_title_match(canonical_ep.title, cand.get("title"))
            if matched:
                return {
                    "id": str(cand.get("id")),
                    "season": cand.get("season"),
                    "episode": cand.get("episode"),
                    "title": cand.get("title"),
                    "overview": cand.get("overview"),
                    "air_date": cand.get("air_date"),
                    "method": method,
                    "confidence": conf,
                    "raw": cand.get("raw", cand)
                }

        # 2. Check all other seasons for title match (cross-season numbering differences)
        for cand in source_episodes_all:
            if cand.get("season") != canonical_ep.season_number:
                matched, method, conf = is_title_match(canonical_ep.title, cand.get("title"))
                if matched:
                    return {
                        "id": str(cand.get("id")),
                        "season": cand.get("season"),
                        "episode": cand.get("episode"),
                        "title": cand.get("title"),
                        "overview": cand.get("overview"),
                        "air_date": cand.get("air_date"),
                        "method": f"{method}_CROSS_SEASON",
                        "confidence": conf * 0.95,
                        "raw": cand.get("raw", cand)
                    }

        # 3. LLM Semantic Match if no exact/fuzzy title match found
        if ollama_url:
            # Build candidate list prioritized: same season candidates first, then adjacent
            llm_candidates = same_season_candidates if same_season_candidates else source_episodes_all[:20]
            if llm_candidates:
                llm_res = await cls.match_source_candidates_with_llm(
                    ollama_url=ollama_url,
                    primary_model=primary_model,
                    fallback_model=fallback_model,
                    canonical_ep=canonical_ep,
                    candidates=llm_candidates,
                    source_name=source_name
                )
                if llm_res and llm_res.get("is_match") and llm_res.get("matched_candidate_id"):
                    target_id = str(llm_res["matched_candidate_id"])
                    chosen = next((c for c in source_episodes_all if str(c.get("id")) == target_id), None)
                    if chosen:
                        conf = float(llm_res.get("confidence", 80)) / 100.0
                        return {
                            "id": str(chosen.get("id")),
                            "season": chosen.get("season"),
                            "episode": chosen.get("episode"),
                            "title": chosen.get("title"),
                            "overview": chosen.get("overview"),
                            "air_date": chosen.get("air_date"),
                            "method": "AI_LLM_CONFIRMED",
                            "confidence": conf,
                            "raw": chosen.get("raw", chosen)
                        }

        return None

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
                try:
                    await db.commit()
                except Exception:
                    pass

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

        # 4. Fetch Complete Source Metadata Repositories
        # A. TMDB
        tmdb_key = config.get("tmdb_api_key")
        all_tmdb_episodes: List[Dict[str, Any]] = []
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
                    # Also include Season 0 specials
                    seasons.add(0)
                    for s in sorted(seasons):
                        eps = await TMDBClient.get_season_episodes(tmdb_key, tmdb_id, s)
                        for t_ep in eps:
                            all_tmdb_episodes.append({
                                "id": str(t_ep.get("id")),
                                "season": t_ep.get("season_number"),
                                "episode": t_ep.get("episode_number"),
                                "title": t_ep.get("name"),
                                "overview": t_ep.get("overview"),
                                "air_date": t_ep.get("air_date"),
                                "raw": t_ep
                            })
                    await log(f"Retrieved {len(all_tmdb_episodes)} total TMDB episodes.")
            except Exception as e:
                await log(f"TMDB fetch error: {str(e)}")

        # B. TVmaze
        all_tvmaze_episodes: List[Dict[str, Any]] = []
        try:
            tvmaze_show = await TVmazeClient.lookup_show(tvdb_id=show.tvdb_id, imdb_id=show.imdb_id, title=show.title)
            if tvmaze_show and tvmaze_show.get("id"):
                show.tvmaze_id = tvmaze_show.get("id")
                raw_tvmaze = await TVmazeClient.get_episodes(show.tvmaze_id)
                for tv_ep in raw_tvmaze:
                    all_tvmaze_episodes.append({
                        "id": str(tv_ep.get("id")),
                        "season": tv_ep.get("season"),
                        "episode": tv_ep.get("number"),
                        "title": tv_ep.get("name"),
                        "overview": re.sub(r"<[^>]+>", "", tv_ep.get("summary") or ""),
                        "air_date": tv_ep.get("airdate"),
                        "raw": tv_ep
                    })
                await log(f"Retrieved {len(all_tvmaze_episodes)} total TVmaze episodes.")
        except Exception as e:
            await log(f"TVmaze fetch error: {str(e)}")

        # C. OMDb
        omdb_key = config.get("omdb_api_key")
        all_omdb_episodes: List[Dict[str, Any]] = []
        if omdb_key and show.imdb_id:
            try:
                seasons = set(ep.season_number for ep in episodes_map.values() if ep.season_number > 0)
                for s in sorted(seasons):
                    omdb_eps = await OMDbClient.get_season_episodes(omdb_key, show.imdb_id, s)
                    for o_ep in omdb_eps:
                        ep_num = int(o_ep.get("Episode", 0)) if str(o_ep.get("Episode", "")).isdigit() else None
                        all_omdb_episodes.append({
                            "id": o_ep.get("imdbID") or f"omdb_S{s}E{ep_num}",
                            "season": s,
                            "episode": ep_num,
                            "title": o_ep.get("Title"),
                            "overview": o_ep.get("Plot"),
                            "air_date": o_ep.get("Released"),
                            "raw": o_ep
                        })
                await log(f"Retrieved {len(all_omdb_episodes)} total OMDb episodes.")
            except Exception as e:
                await log(f"OMDb fetch error: {str(e)}")

        # D. Subtitles / Transcripts
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
                job.message = f"Matching episode {current_idx}/{total_eps}: S{canonical_ep.season_number}E{canonical_ep.episode_number} - {canonical_ep.title}"
                if current_idx % 5 == 0 or current_idx == total_eps:
                    try:
                        await db.commit()
                    except Exception:
                        pass

            # 1. Match TMDB
            matched_tmdb = await cls.match_episode_against_source(
                canonical_ep=canonical_ep,
                source_episodes_all=all_tmdb_episodes,
                source_name="TMDB",
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model
            )
            if matched_tmdb:
                # Check if already added
                stmt_exist = select(EpisodeSourceMetadata).where(
                    EpisodeSourceMetadata.episode_id == canonical_ep.id,
                    EpisodeSourceMetadata.source_name == "tmdb"
                )
                res_exist = await db.execute(stmt_exist)
                existing_meta = res_exist.scalars().first()
                if not existing_meta:
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

            # 2. Match TVmaze
            matched_tvmaze = await cls.match_episode_against_source(
                canonical_ep=canonical_ep,
                source_episodes_all=all_tvmaze_episodes,
                source_name="TVmaze",
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model
            )
            if matched_tvmaze:
                stmt_exist = select(EpisodeSourceMetadata).where(
                    EpisodeSourceMetadata.episode_id == canonical_ep.id,
                    EpisodeSourceMetadata.source_name == "tvmaze"
                )
                res_exist = await db.execute(stmt_exist)
                existing_meta = res_exist.scalars().first()
                if not existing_meta:
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

            # 3. Match OMDb
            matched_omdb = await cls.match_episode_against_source(
                canonical_ep=canonical_ep,
                source_episodes_all=all_omdb_episodes,
                source_name="OMDb",
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model
            )
            if matched_omdb:
                stmt_exist = select(EpisodeSourceMetadata).where(
                    EpisodeSourceMetadata.episode_id == canonical_ep.id,
                    EpisodeSourceMetadata.source_name == "omdb"
                )
                res_exist = await db.execute(stmt_exist)
                existing_meta = res_exist.scalars().first()
                if not existing_meta:
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

            # 4. SubDL Subtitles
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
                        stmt_exist = select(EpisodeSourceMetadata).where(
                            EpisodeSourceMetadata.episode_id == canonical_ep.id,
                            EpisodeSourceMetadata.source_name == "subdl"
                        )
                        res_exist = await db.execute(stmt_exist)
                        existing_meta = res_exist.scalars().first()
                        if not existing_meta:
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
