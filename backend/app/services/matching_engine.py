import json
import re
import difflib
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.models.show import Show, Episode, EpisodeSourceMetadata
from backend.app.models.job import Job
from backend.app.services.sonarr_client import SonarrClient
from backend.app.services.tmdb_client import TMDBClient
from backend.app.services.tvmaze_client import TVmazeClient
from backend.app.services.omdb_client import OMDbClient
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


def extract_title_aliases(title: Optional[str]) -> List[str]:
    """
    Extracts base title, parenthetical text, and subtitle segments for resilient matching.
    Example: 'Racing The Storm (American Airlines, Flight 1420)' ->
    ['Racing The Storm (American Airlines, Flight 1420)', 'Racing The Storm', 'American Airlines, Flight 1420']
    """
    if not title:
        return []
    aliases = [title.strip()]

    # 1. Parenthetical extraction: "Title (Subtitle)"
    parentheses = re.findall(r"\((.*?)\)", title)
    base_no_parens = re.sub(r"\(.*?\)", "", title).strip()
    if base_no_parens and base_no_parens not in aliases:
        aliases.append(base_no_parens)
    for p in parentheses:
        p_clean = p.strip()
        if p_clean and p_clean not in aliases:
            aliases.append(p_clean)

    # 2. Colon / Hyphen separators: "Title: Subtitle" or "Title - Subtitle"
    for sep in [":", " - "]:
        if sep in title:
            parts = title.split(sep)
            for part in parts:
                p_clean = part.strip()
                if p_clean and p_clean not in aliases:
                    aliases.append(p_clean)

    return aliases


def is_title_match(t1: Optional[str], t2: Optional[str]) -> Tuple[bool, str, float]:
    """
    Returns (is_match, match_method, confidence) across title aliases.
    """
    if not t1 or not t2:
        return False, "NONE", 0.0

    aliases1 = extract_title_aliases(t1)
    aliases2 = extract_title_aliases(t2)

    for a1 in aliases1:
        n1 = normalize_title(a1)
        s1 = token_sorted_title(a1)
        for a2 in aliases2:
            n2 = normalize_title(a2)
            s2 = token_sorted_title(a2)

            if n1 and n2 and n1 == n2:
                return True, "EXACT_TITLE", 1.0

            if s1 and s2 and s1 == s2:
                return True, "EXACT_TITLE_REORDERED", 0.98

            sim = difflib.SequenceMatcher(None, n1, n2).ratio()
            if sim >= 0.88:
                return True, "FUZZY_TITLE_MATCH", round(sim, 2)

            s_sim = difflib.SequenceMatcher(None, s1, s2).ratio()
            if s_sim >= 0.88:
                return True, "FUZZY_TITLE_MATCH", round(s_sim, 2)

    return False, "NONE", 0.0


class SourceIndex:
    """Pre-indexed repository for fast candidate proposal across a source database."""
    def __init__(self, source_name: str, episodes: List[Dict[str, Any]]):
        self.source_name = source_name
        self.episodes = episodes
        self.by_exact_title: Dict[str, Dict[str, Any]] = {}
        self.by_token_title: Dict[str, Dict[str, Any]] = {}
        self.by_season: Dict[int, List[Dict[str, Any]]] = {}
        self.by_season_ep: Dict[Tuple[int, int], Dict[str, Any]] = {}

        for ep in episodes:
            t = ep.get("title")
            if t:
                for alias in extract_title_aliases(t):
                    norm = normalize_title(alias)
                    tok = token_sorted_title(alias)
                    if norm and norm not in self.by_exact_title:
                        self.by_exact_title[norm] = ep
                    if tok and tok not in self.by_token_title:
                        self.by_token_title[tok] = ep
            
            s = ep.get("season")
            e = ep.get("episode")
            if s is not None:
                self.by_season.setdefault(s, []).append(ep)
                if e is not None:
                    self.by_season_ep[(s, e)] = ep

    def propose_candidate(self, canonical_ep: Episode) -> Optional[Dict[str, Any]]:
        """Proposes the most plausible candidate using fuzzy, alias, or season indexing."""
        # 1. Check title aliases
        for alias in extract_title_aliases(canonical_ep.title):
            norm = normalize_title(alias)
            tok = token_sorted_title(alias)
            if norm in self.by_exact_title:
                return self.by_exact_title[norm]
            if tok in self.by_token_title:
                return self.by_token_title[tok]

        # 2. Same-season fuzzy match
        same_season_eps = self.by_season.get(canonical_ep.season_number, [])
        for cand in same_season_eps:
            matched, _, _ = is_title_match(canonical_ep.title, cand.get("title"))
            if matched:
                return cand

        # 3. Cross-season fuzzy match
        for cand in self.episodes:
            matched, _, _ = is_title_match(canonical_ep.title, cand.get("title"))
            if matched:
                return cand

        # 4. Same season and episode index
        if (canonical_ep.season_number, canonical_ep.episode_number) in self.by_season_ep:
            return self.by_season_ep[(canonical_ep.season_number, canonical_ep.episode_number)]

        # 5. Air date match in the same season
        if canonical_ep.air_date:
            for cand in same_season_eps:
                if cand.get("air_date") == canonical_ep.air_date:
                    return cand

        return None

    def get_search_pool(self, season_number: int) -> List[Dict[str, Any]]:
        """Returns candidate pool for searching."""
        if season_number == 0:
            return self.by_season.get(0, [])

        if len(self.episodes) <= 40:
            return self.episodes

        pool = []
        pool.extend(self.by_season.get(season_number, []))
        for offset in [-1, 1, -2, 2]:
            adj_season = season_number + offset
            if adj_season > 0:
                pool.extend(self.by_season.get(adj_season, []))

        if not pool:
            return self.episodes[:30]
        return pool

    def get_by_season_episode(self, season_number: int, episode_number: int) -> Optional[Dict[str, Any]]:
        return self.by_season_ep.get((season_number, episode_number))


class MatchingEngine:
    @staticmethod
    async def ai_confirm_match(
        ollama_url: str,
        primary_model: str,
        fallback_model: Optional[str],
        show_title: str,
        canonical_ep: Episode,
        candidate: Dict[str, Any],
        source_name: str
    ) -> Tuple[bool, str]:
        """
        Step 2: AI Episode Confirmation Prompt
        Prompts the LLM: 'please answer in the form of one word: yes or no, are these episodes a match?'
        Returns: (is_confirmed: bool, model_used: str)
        """
        if not ollama_url:
            return True, "OLLAMA_DISABLED"

        sonarr_s = canonical_ep.season_number if canonical_ep.season_number is not None else 0
        sonarr_e = canonical_ep.episode_number if canonical_ep.episode_number is not None else 0
        sonarr_title = canonical_ep.title or ""
        sonarr_desc = canonical_ep.overview or "N/A"
        sonarr_date = canonical_ep.air_date or "N/A"

        cand_s = candidate.get("season") if candidate.get("season") is not None else 0
        cand_e = candidate.get("episode") if candidate.get("episode") is not None else 0
        cand_title = candidate.get("title") or ""
        cand_desc = candidate.get("overview") or "N/A"
        cand_date = candidate.get("air_date") or "N/A"

        prompt = (
            f"you are being used to programatically confirm an episode matching of the show {show_title} from {source_name} to sonarr's episode for {show_title}\n\n"
            f"    here is all the episode info from sonarr:\n\n"
            f"        Season / Episode: S{sonarr_s:02d}E{sonarr_e:02d}\n"
            f"        Title: {sonarr_title}\n"
            f"        Description: {sonarr_desc}\n"
            f"        Air Date: {sonarr_date}\n\n"
            f"    here is all the episode info from the suspected match:\n\n"
            f"        Season / Episode: S{cand_s:02d}E{cand_e:02d}\n"
            f"        Title: {cand_title}\n"
            f"        Description: {cand_desc}\n"
            f"        Air Date: {cand_date}\n\n"
            f" please answer in the form of one word: yes or no, are these episodes a match?"
        )

        system_prompt = (
            "You are an expert TV episode matching engine. Determine if the two entries represent the exact same episode/story. "
            "Please answer in the form of one word: yes or no."
        )

        def is_yes_validator(text: str) -> bool:
            clean = text.strip().lower()
            return clean.startswith("yes") or "yes" in clean.split() or clean == "true"

        resp_text, is_valid, model_used = await OllamaClient.execute_prompt_with_2try_fallback(
            base_url=ollama_url,
            primary_model=primary_model,
            fallback_model=fallback_model,
            user_prompt=prompt,
            validator_fn=is_yes_validator,
            system_prompt=system_prompt
        )

        return is_valid, model_used

    @staticmethod
    async def ai_search_candidates(
        ollama_url: str,
        primary_model: str,
        fallback_model: Optional[str],
        show_title: str,
        canonical_ep: Episode,
        candidates: List[Dict[str, Any]],
        source_name: str
    ) -> Tuple[Optional[Tuple[int, int]], str]:
        """
        Step 3: AI Candidate List Search Prompt
        Prompts the LLM: 'please answer in the form of SxxEyy (using the sources numbering scheme): which episode is a match for the provided sonarr episode?'
        Returns: ((season_number, episode_number) or None, model_used)
        """
        if not ollama_url or not candidates:
            return None, "NO_CANDIDATES"

        sonarr_s = canonical_ep.season_number if canonical_ep.season_number is not None else 0
        sonarr_e = canonical_ep.episode_number if canonical_ep.episode_number is not None else 0
        sonarr_title = canonical_ep.title or ""
        sonarr_desc = canonical_ep.overview or "N/A"
        sonarr_date = canonical_ep.air_date or "N/A"

        cand_lines = []
        for c in candidates[:30]:
            cs = c.get("season") if c.get("season") is not None else 0
            ce = c.get("episode") if c.get("episode") is not None else 0
            ct = c.get("title") or ""
            cd = (c.get("overview") or "N/A")[:180]
            cdate = c.get("air_date") or "N/A"
            cand_lines.append(
                f"        S{cs:02d}E{ce:02d} from {source_name}\n"
                f"            Title: {ct}\n"
                f"            Description: {cd}\n"
                f"            Air Date: {cdate}"
            )

        cand_list_str = "\n\n".join(cand_lines)

        prompt = (
            f"you are being used to programatically find an episode matching of the show {show_title} from {source_name} to sonarr's episode for {show_title}\n\n"
            f"    here is all the episode info from sonarr:\n\n"
            f"        Season / Episode: S{sonarr_s:02d}E{sonarr_e:02d}\n"
            f"        Title: {sonarr_title}\n"
            f"        Description: {sonarr_desc}\n"
            f"        Air Date: {sonarr_date}\n\n"
            f"    here is list of each episode and all the episode info from the suspected match:\n\n"
            f"{cand_list_str}\n\n"
            f" please answer in the form of SxxEyy (using the sources numbering scheme): which episode is a match for the provided sonarr episode?"
        )

        system_prompt = (
            "You are an expert TV episode matching engine. Identify the matching episode from the list. "
            "Please answer in the form of SxxEyy (using the sources numbering scheme): which episode is a match for the provided sonarr episode? "
            "If none matches, answer NONE."
        )

        def has_valid_se_validator(text: str) -> bool:
            return bool(re.search(r"S(\d+)E(\d+)", text, re.IGNORECASE))

        resp_text, is_valid, model_used = await OllamaClient.execute_prompt_with_2try_fallback(
            base_url=ollama_url,
            primary_model=primary_model,
            fallback_model=fallback_model,
            user_prompt=prompt,
            validator_fn=has_valid_se_validator,
            system_prompt=system_prompt
        )

        if is_valid:
            match = re.search(r"S(\d+)E(\d+)", resp_text, re.IGNORECASE)
            if match:
                s_num = int(match.group(1))
                e_num = int(match.group(2))
                return (s_num, e_num), model_used

        return None, model_used

    @classmethod
    async def match_single_source_episode(
        cls,
        show_title: str,
        canonical_ep: Episode,
        source_index: SourceIndex,
        ollama_url: str,
        primary_model: str,
        fallback_model: Optional[str]
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """
        Executes the 2-attempt Confirmation -> Search -> Verify loop.
        Returns: (matched_variation_dict_or_None, requires_user_intervention: bool)
        """
        if not source_index.episodes:
            return None, False

        for attempt in range(1, 3):
            # 1. Propose candidate using traditional methods (fuzzy / alias / indexing)
            candidate = source_index.propose_candidate(canonical_ep)

            if candidate:
                is_match, model_used = await cls.ai_confirm_match(
                    ollama_url=ollama_url,
                    primary_model=primary_model,
                    fallback_model=fallback_model,
                    show_title=show_title,
                    canonical_ep=canonical_ep,
                    candidate=candidate,
                    source_name=source_index.source_name
                )
                if is_match:
                    return {
                        "id": str(candidate.get("id")),
                        "season": candidate.get("season"),
                        "episode": candidate.get("episode"),
                        "title": candidate.get("title"),
                        "overview": candidate.get("overview"),
                        "air_date": candidate.get("air_date"),
                        "method": "AI_CONFIRMED_MATCH",
                        "confidence": 1.0,
                        "model_used": model_used,
                        "raw": candidate.get("raw", candidate)
                    }, False

            # 2. If confirmation failed or no candidate, run Candidate List Search
            cands_pool = source_index.get_search_pool(canonical_ep.season_number)
            se_result, model_used = await cls.ai_search_candidates(
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model,
                show_title=show_title,
                canonical_ep=canonical_ep,
                candidates=cands_pool,
                source_name=source_index.source_name
            )

            if se_result:
                s_num, e_num = se_result
                chosen = source_index.get_by_season_episode(s_num, e_num)
                if chosen:
                    # Re-verify chosen candidate with Phase 1 confirmation prompt
                    is_match, model_used = await cls.ai_confirm_match(
                        ollama_url=ollama_url,
                        primary_model=primary_model,
                        fallback_model=fallback_model,
                        show_title=show_title,
                        canonical_ep=canonical_ep,
                        candidate=chosen,
                        source_name=source_index.source_name
                    )
                    if is_match:
                        return {
                            "id": str(chosen.get("id")),
                            "season": chosen.get("season"),
                            "episode": chosen.get("episode"),
                            "title": chosen.get("title"),
                            "overview": chosen.get("overview"),
                            "air_date": chosen.get("air_date"),
                            "method": "AI_SEARCH_CONFIRMED",
                            "confidence": 0.95,
                            "model_used": model_used,
                            "raw": chosen.get("raw", chosen)
                        }, False

        # After 2 failed attempts, mark as requiring user intervention
        return None, True

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

        # D. Build Pre-Indexed Repositories
        tmdb_index = SourceIndex("TMDB", all_tmdb_episodes)
        tvmaze_index = SourceIndex("TVmaze", all_tvmaze_episodes)
        omdb_index = SourceIndex("OMDb", all_omdb_episodes)

        # 5. Matching Loop with Sequential AI Decisions
        ollama_url = config.get("ollama_url", "http://localhost:11434")
        primary_model = config.get("ollama_primary_model", "gemma4:e4b")
        fallback_model = config.get("ollama_fallback_model", "gemma4-obliterated:latest")

        total_eps = len(episodes_map)
        current_idx = 0

        for ep_id, canonical_ep in episodes_map.items():
            current_idx += 1
            if job:
                job.progress = round((current_idx / total_eps) * 80.0, 1)
                job.message = f"Matching episode {current_idx}/{total_eps}: S{canonical_ep.season_number}E{canonical_ep.episode_number} - {canonical_ep.title}"
                try:
                    await db.commit()
                except Exception:
                    pass

            ep_sources_matched = 0
            ep_requires_intervention = False

            # 1. Match TMDB
            matched_tmdb, interv_tmdb = await cls.match_single_source_episode(
                show_title=show.title,
                canonical_ep=canonical_ep,
                source_index=tmdb_index,
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model
            )
            if matched_tmdb:
                ep_sources_matched += 1
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
            elif interv_tmdb and len(all_tmdb_episodes) > 0:
                ep_requires_intervention = True

            # 2. Match TVmaze
            matched_tvmaze, interv_tvmaze = await cls.match_single_source_episode(
                show_title=show.title,
                canonical_ep=canonical_ep,
                source_index=tvmaze_index,
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model
            )
            if matched_tvmaze:
                ep_sources_matched += 1
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
            elif interv_tvmaze and len(all_tvmaze_episodes) > 0:
                ep_requires_intervention = True

            # 3. Match OMDb
            matched_omdb, interv_omdb = await cls.match_single_source_episode(
                show_title=show.title,
                canonical_ep=canonical_ep,
                source_index=omdb_index,
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model
            )
            if matched_omdb:
                ep_sources_matched += 1
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
            elif interv_omdb and len(all_omdb_episodes) > 0:
                ep_requires_intervention = True

            # Update canonical episode verification status
            if ep_sources_matched > 0:
                canonical_ep.ai_verification_status = "AI_MATCHED"
                canonical_ep.ai_confidence_score = 100.0
                canonical_ep.ai_audit_notes = f"AI confirmed across {ep_sources_matched} external sources."
            elif ep_requires_intervention:
                canonical_ep.ai_verification_status = "REQUIRES_USER_INTERVENTION"
                canonical_ep.ai_confidence_score = 40.0
                canonical_ep.ai_audit_notes = "No AI match confirmed after 2 attempts. Requires user review."
            else:
                canonical_ep.ai_verification_status = "PENDING"

        await db.commit()
        await log("Completed multi-source episode matching pass.")
        return show
