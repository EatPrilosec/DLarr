import json
import re
import difflib
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable, Set
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
    async def ai_step0_detect_no_match(
        ollama_url: str,
        primary_model: str,
        fallback_model: Optional[str],
        show_title: str,
        source_name: str,
        sonarr_eps: List[Episode],
        source_eps: List[Dict[str, Any]]
    ) -> Set[Tuple[int, int]]:
        """
        Step 0: AI No Match Marking
        When source has fewer episodes than Sonarr, asks primary and fallback models
        to identify Sonarr episodes that definitely cannot exist on this source.
        Returns: set of (season_number, episode_number) agreed by BOTH models.
        """
        if not ollama_url or not source_eps or len(source_eps) >= len(sonarr_eps):
            return set()

        sonarr_lines = [
            f"S{ep.season_number:02d}E{ep.episode_number:02d} - {ep.title or 'Unknown'} (Air Date: {ep.air_date or 'N/A'})"
            for ep in sonarr_eps[:120]
        ]
        source_lines = [
            f"S{(c.get('season') or 0):02d}E{(c.get('episode') or 0):02d} - {c.get('title') or 'Unknown'} (Air Date: {c.get('air_date') or 'N/A'})"
            for c in source_eps[:120]
        ]

        prompt = (
            f"you are being used to programatically identify Sonarr episodes that have no corresponding episode in {source_name} for the show {show_title}.\n\n"
            f"here is the list of episodes from Sonarr ({len(sonarr_eps)} total):\n"
            + "\n".join(sonarr_lines) + "\n\n"
            f"here is the complete list of episodes available from {source_name} ({len(source_eps)} total):\n"
            + "\n".join(source_lines) + "\n\n"
            f"please answer in the form of a comma-separated list of Sonarr episodes in SxxEyy format that definitely do NOT exist in {source_name}. If all episodes might have a match, answer NONE."
        )

        system_prompt = (
            "You are an expert TV metadata auditor. Identify Sonarr episodes (like specials or missing seasons) that do not exist in the source list. "
            "Output only a comma-separated list of SxxEyy (e.g. S00E01, S00E02) or NONE."
        )

        # 1. Query Primary Model
        resp_p, _ = await OllamaClient.query_with_retry_and_fallback(
            base_url=ollama_url,
            primary_model=primary_model,
            fallback_model=None,
            user_prompt=prompt,
            system_prompt=system_prompt
        )
        matches_p = set((int(s), int(e)) for s, e in re.findall(r"S(\d+)E(\d+)", resp_p, re.IGNORECASE))

        if not matches_p or not fallback_model or fallback_model == primary_model:
            return matches_p

        # 2. Query Fallback Model
        resp_f, _ = await OllamaClient.query_with_retry_and_fallback(
            base_url=ollama_url,
            primary_model=fallback_model,
            fallback_model=None,
            user_prompt=prompt,
            system_prompt=system_prompt
        )
        matches_f = set((int(s), int(e)) for s, e in re.findall(r"S(\d+)E(\d+)", resp_f, re.IGNORECASE))

        # Both models must agree
        agreed = matches_p.intersection(matches_f)
        return agreed

    @staticmethod
    async def ai_batch_confirm_matches(
        ollama_url: str,
        primary_model: str,
        fallback_model: Optional[str],
        show_title: str,
        source_name: str,
        batch_pairs: List[Tuple[Episode, Dict[str, Any]]]
    ) -> Tuple[Set[int], str]:
        """
        Step 1A/1B/2B: 10-Episode Batch High-Context Confirmation Prompt
        Prompts LLM to confirm all 10 comparisons with 'yes', or output comma-separated failed SxxEyy.
        Returns: (confirmed_canonical_ids: Set[int], model_used: str)
        """
        if not batch_pairs:
            return set(), "NO_PAIRS"
        if not ollama_url:
            return set(ep.id for ep, _ in batch_pairs), "OLLAMA_DISABLED"

        items = []
        for idx, (s_ep, cand) in enumerate(batch_pairs, 1):
            s_s = s_ep.season_number if s_ep.season_number is not None else 0
            s_e = s_ep.episode_number if s_ep.episode_number is not None else 0
            c_s = cand.get("season") if cand.get("season") is not None else 0
            c_e = cand.get("episode") if cand.get("episode") is not None else 0

            items.append(
                f"[Item {idx}]\n"
                f"Sonarr: S{s_s:02d}E{s_e:02d} - \"{s_ep.title or ''}\" | Air Date: {s_ep.air_date or 'N/A'} | Overview: {(s_ep.overview or 'N/A')[:140]}\n"
                f"{source_name}: S{c_s:02d}E{c_e:02d} - \"{cand.get('title') or ''}\" | Air Date: {cand.get('air_date') or 'N/A'} | Overview: {(cand.get('overview') or 'N/A')[:140]}"
            )

        items_str = "\n\n".join(items)

        prompt = (
            f"you are being used to programatically confirm episode matches of the show {show_title} from {source_name} to Sonarr's episodes for {show_title}.\n\n"
            f"here are the {len(batch_pairs)} proposed episode comparisons:\n\n"
            f"{items_str}\n\n"
            f"please answer with \"yes\" if all {len(batch_pairs)} episodes are valid matches. If any are NOT matches, answer with a comma-separated list of the failed {source_name} episode numbers in SxxEyy format (e.g. S01E03, S01E08)."
        )

        system_prompt = (
            "You are an expert TV episode matching engine. Confirm if the proposed episodes represent the exact same story. "
            "Answer 'yes' if all match, or output a comma-separated list of failed SxxEyy episodes."
        )

        resp_text, model_used = await OllamaClient.query_with_retry_and_fallback(
            base_url=ollama_url,
            primary_model=primary_model,
            fallback_model=fallback_model,
            user_prompt=prompt,
            system_prompt=system_prompt
        )

        clean = resp_text.strip().lower()
        failed_ses = set((int(s), int(e)) for s, e in re.findall(r"S(\d+)E(\d+)", resp_text, re.IGNORECASE))

        confirmed_ids: Set[int] = set()
        if not failed_ses and (clean.startswith("yes") or "yes" in clean.split() or clean == "true"):
            # All 10 confirmed
            for s_ep, _ in batch_pairs:
                confirmed_ids.add(s_ep.id)
        elif failed_ses:
            # Partial confirmation
            for s_ep, cand in batch_pairs:
                cs = cand.get("season") if cand.get("season") is not None else 0
                ce = cand.get("episode") if cand.get("episode") is not None else 0
                if (cs, ce) not in failed_ses:
                    confirmed_ids.add(s_ep.id)
        elif clean.startswith("yes"):
            for s_ep, _ in batch_pairs:
                confirmed_ids.add(s_ep.id)

        return confirmed_ids, model_used

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
        Step 2A: AI Candidate List Search Prompt (Single Episode)
        Prompts LLM to select matching SxxEyy from remaining unmapped source pool.
        """
        if not ollama_url or not candidates:
            return None, "NO_CANDIDATES"

        sonarr_s = canonical_ep.season_number if canonical_ep.season_number is not None else 0
        sonarr_e = canonical_ep.episode_number if canonical_ep.episode_number is not None else 0
        sonarr_title = canonical_ep.title or ""
        sonarr_desc = canonical_ep.overview or "N/A"
        sonarr_date = canonical_ep.air_date or "N/A"

        cand_lines = []
        for c in candidates[:35]:
            cs = c.get("season") if c.get("season") is not None else 0
            ce = c.get("episode") if c.get("episode") is not None else 0
            ct = c.get("title") or ""
            cd = (c.get("overview") or "N/A")[:140]
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
            f" please answer in the form of SxxEyy (using the sources numbering scheme): which episode is a match for the provided sonarr episode? If none matches, answer NONE."
        )

        system_prompt = (
            "You are an expert TV episode matching engine. Identify the matching episode from the list. "
            "Please answer in the form of SxxEyy (using the sources numbering scheme): which episode is a match for the provided sonarr episode? "
            "If none matches, answer NONE."
        )

        resp_text, model_used = await OllamaClient.query_with_retry_and_fallback(
            base_url=ollama_url,
            primary_model=primary_model,
            fallback_model=fallback_model,
            user_prompt=prompt,
            system_prompt=system_prompt
        )

        match = re.search(r"S(\d+)E(\d+)", resp_text, re.IGNORECASE)
        if match:
            s_num = int(match.group(1))
            e_num = int(match.group(2))
            return (s_num, e_num), model_used

        return None, model_used

    @classmethod
    async def match_source_multistage(
        cls,
        db: AsyncSession,
        show: Show,
        canonical_episodes: List[Episode],
        source_name: str,
        source_episodes: List[Dict[str, Any]],
        ollama_url: str,
        primary_model: str,
        fallback_model: Optional[str],
        log_cb: Optional[Callable[[str], Awaitable[None]]] = None
    ):
        """
        Executes Step 0, Step 1A, Step 1B, Step 2A, Step 2B, Step 2C for a single source.
        """
        from backend.app.services.concurrency_manager import concurrency_manager

        async def log(msg: str):
            if log_cb:
                await log_cb(msg)

        if not source_episodes:
            await log(f"[{source_name}] No external episodes available to match.")
            return

        mapped_source_keys: Set[Tuple[int, int]] = set()
        mapped_canonical_ids: Set[int] = set()

        # Step 0: AI No Match Marking
        await log(f"[{source_name}] Step 0: Checking for non-existent episodes (source count: {len(source_episodes)} vs Sonarr: {len(canonical_episodes)})...")
        no_match_pairs = await cls.ai_step0_detect_no_match(
            ollama_url=ollama_url,
            primary_model=primary_model,
            fallback_model=fallback_model,
            show_title=show.title,
            source_name=source_name,
            sonarr_eps=canonical_episodes,
            source_eps=source_episodes
        )

        for ep in canonical_episodes:
            if (ep.season_number, ep.episode_number) in no_match_pairs:
                mapped_canonical_ids.add(ep.id)
                meta = EpisodeSourceMetadata(
                    episode_id=ep.id,
                    show_id=show.id,
                    source_name=source_name.lower(),
                    source_show_id=str(show.tmdb_id if source_name == 'TMDB' else (show.tvmaze_id if source_name == 'TVmaze' else show.imdb_id)),
                    source_episode_id=f"no_match_{ep.id}",
                    source_season_number=None,
                    source_episode_number=None,
                    title="No Matching Episode in Source",
                    overview="AI confirmed that this Sonarr episode does not exist in this source database.",
                    air_date=ep.air_date,
                    match_method="NO_MATCH",
                    match_confidence=0.0,
                    raw_metadata=json.dumps({"reason": "STEP0_AI_CONFIRMED_ABSENT"})
                )
                db.add(meta)

        if no_match_pairs:
            await log(f"[{source_name}] Step 0: Marked {len(no_match_pairs)} episodes as NO_MATCH.")

        # Step 1A: Strict 1:1 Title Matching
        await log(f"[{source_name}] Step 1A: Running strict title matching across entire source repository...")
        # Map normalized title -> candidate
        source_by_strict_title: Dict[str, List[Dict[str, Any]]] = {}
        for cand in source_episodes:
            t = cand.get("title")
            if t:
                for alias in extract_title_aliases(t):
                    n = normalize_title(alias)
                    if n:
                        source_by_strict_title.setdefault(n, []).append(cand)

        proposed_1a: List[Tuple[Episode, Dict[str, Any]]] = []
        for ep in canonical_episodes:
            if ep.id in mapped_canonical_ids:
                continue
            for alias in extract_title_aliases(ep.title):
                n = normalize_title(alias)
                cands = source_by_strict_title.get(n, [])
                if len(cands) == 1:
                    cand = cands[0]
                    ckey = (cand.get("season") or 0, cand.get("episode") or 0)
                    if ckey not in mapped_source_keys:
                        proposed_1a.append((ep, cand))
                        break

        # Batch confirm 1A in 10-episode groups
        for i in range(0, len(proposed_1a), 10):
            batch = proposed_1a[i:i+10]
            confirmed_ids, model_used = await cls.ai_batch_confirm_matches(
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model,
                show_title=show.title,
                source_name=source_name,
                batch_pairs=batch
            )
            for ep, cand in batch:
                if ep.id in confirmed_ids:
                    ckey = (cand.get("season") or 0, cand.get("episode") or 0)
                    mapped_source_keys.add(ckey)
                    mapped_canonical_ids.add(ep.id)
                    meta = EpisodeSourceMetadata(
                        episode_id=ep.id,
                        show_id=show.id,
                        source_name=source_name.lower(),
                        source_show_id=str(show.tmdb_id if source_name == 'TMDB' else (show.tvmaze_id if source_name == 'TVmaze' else show.imdb_id)),
                        source_episode_id=str(cand.get("id")),
                        source_season_number=cand.get("season"),
                        source_episode_number=cand.get("episode"),
                        title=cand.get("title"),
                        overview=cand.get("overview"),
                        air_date=cand.get("air_date"),
                        match_method="PERFECT_MATCH",
                        match_confidence=1.0,
                        raw_metadata=json.dumps(cand.get("raw", cand))
                    )
                    db.add(meta)

        await db.commit()
        await log(f"[{source_name}] Step 1A: Confirmed {len(mapped_canonical_ids)} strict matches.")

        # Step 1B: Loose Title Matching (Punctuation & Parentheticals)
        await log(f"[{source_name}] Step 1B: Running loose / parenthetical title matching...")
        unmapped_source_eps = [c for c in source_episodes if (c.get("season") or 0, c.get("episode") or 0) not in mapped_source_keys]
        proposed_1b: List[Tuple[Episode, Dict[str, Any]]] = []

        for ep in canonical_episodes:
            if ep.id in mapped_canonical_ids:
                continue
            for cand in unmapped_source_eps:
                ckey = (cand.get("season") or 0, cand.get("episode") or 0)
                if ckey in mapped_source_keys:
                    continue
                matched, _, _ = is_title_match(ep.title, cand.get("title"))
                if matched:
                    proposed_1b.append((ep, cand))
                    break

        # Batch confirm 1B in 10-episode groups
        for i in range(0, len(proposed_1b), 10):
            batch = proposed_1b[i:i+10]
            confirmed_ids, model_used = await cls.ai_batch_confirm_matches(
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model,
                show_title=show.title,
                source_name=source_name,
                batch_pairs=batch
            )
            for ep, cand in batch:
                if ep.id in confirmed_ids:
                    ckey = (cand.get("season") or 0, cand.get("episode") or 0)
                    mapped_source_keys.add(ckey)
                    mapped_canonical_ids.add(ep.id)
                    meta = EpisodeSourceMetadata(
                        episode_id=ep.id,
                        show_id=show.id,
                        source_name=source_name.lower(),
                        source_show_id=str(show.tmdb_id if source_name == 'TMDB' else (show.tvmaze_id if source_name == 'TVmaze' else show.imdb_id)),
                        source_episode_id=str(cand.get("id")),
                        source_season_number=cand.get("season"),
                        source_episode_number=cand.get("episode"),
                        title=cand.get("title"),
                        overview=cand.get("overview"),
                        air_date=cand.get("air_date"),
                        match_method="LOOSE_AI_CONFIRMED",
                        match_confidence=0.95,
                        raw_metadata=json.dumps(cand.get("raw", cand))
                    )
                    db.add(meta)

        await db.commit()
        await log(f"[{source_name}] Step 1B: Confirmed {len(mapped_canonical_ids)} total matches.")

        # Step 2A & 2B: AI Candidate Search & Batch Confirmation
        remaining_unmapped_sonarr = [ep for ep in canonical_episodes if ep.id not in mapped_canonical_ids]
        if remaining_unmapped_sonarr:
            await log(f"[{source_name}] Step 2A: Searching remaining {len(remaining_unmapped_sonarr)} unmapped episodes with LLM search...")
            remaining_pool = [c for c in source_episodes if (c.get("season") or 0, c.get("episode") or 0) not in mapped_source_keys]

            source_index = SourceIndex(source_name, remaining_pool)
            proposed_2a: List[Tuple[Episode, Dict[str, Any]]] = []

            for ep in remaining_unmapped_sonarr:
                se_res, _ = await cls.ai_search_candidates(
                    ollama_url=ollama_url,
                    primary_model=primary_model,
                    fallback_model=fallback_model,
                    show_title=show.title,
                    canonical_ep=ep,
                    candidates=remaining_pool,
                    source_name=source_name
                )
                if se_res:
                    s_num, e_num = se_res
                    cand = source_index.get_by_season_episode(s_num, e_num)
                    if cand:
                        proposed_2a.append((ep, cand))

            # Batch confirm Step 2A proposals (Step 2B)
            if proposed_2a:
                await log(f"[{source_name}] Step 2B: Confirming {len(proposed_2a)} search proposals in 10-episode batches...")
                for i in range(0, len(proposed_2a), 10):
                    batch = proposed_2a[i:i+10]
                    confirmed_ids, model_used = await cls.ai_batch_confirm_matches(
                        ollama_url=ollama_url,
                        primary_model=primary_model,
                        fallback_model=fallback_model,
                        show_title=show.title,
                        source_name=source_name,
                        batch_pairs=batch
                    )
                    for ep, cand in batch:
                        if ep.id in confirmed_ids:
                            ckey = (cand.get("season") or 0, cand.get("episode") or 0)
                            mapped_source_keys.add(ckey)
                            mapped_canonical_ids.add(ep.id)
                            meta = EpisodeSourceMetadata(
                                episode_id=ep.id,
                                show_id=show.id,
                                source_name=source_name.lower(),
                                source_show_id=str(show.tmdb_id if source_name == 'TMDB' else (show.tvmaze_id if source_name == 'TVmaze' else show.imdb_id)),
                                source_episode_id=str(cand.get("id")),
                                source_season_number=cand.get("season"),
                                source_episode_number=cand.get("episode"),
                                title=cand.get("title"),
                                overview=cand.get("overview"),
                                air_date=cand.get("air_date"),
                                match_method="AI_SEARCH_CONFIRMED",
                                match_confidence=0.90,
                                raw_metadata=json.dumps(cand.get("raw", cand))
                            )
                            db.add(meta)

                await db.commit()

        await log(f"[{source_name}] Completed multi-stage matching pass ({len(mapped_canonical_ids)}/{len(canonical_episodes)} mapped).")

    @classmethod
    async def process_show_ingestion(
        cls,
        db: AsyncSession,
        sonarr_series_id: int,
        config: Dict[str, Any],
        job: Optional[Job] = None,
    ) -> Show:
        """
        Full show ingestion orchestrator executing the Multi-Stage Batch & Search Matching Pipeline.
        """
        from backend.app.services.concurrency_manager import concurrency_manager

        async def log(msg: str):
            if job:
                job.logs = (job.logs or "") + f"\n{msg}"
                job.message = msg[:250]
                try:
                    await db.commit()
                except Exception:
                    pass

        await log(f"Starting multi-stage ingestion for Sonarr series ID {sonarr_series_id}...")

        # 1. Fetch from Sonarr
        sonarr_url = config.get("sonarr_url")
        sonarr_key = config.get("sonarr_api_key")
        if not sonarr_url or not sonarr_key:
            raise ValueError("Sonarr URL or API Key is missing in configuration.")

        series_data = await SonarrClient.get_series_detail(sonarr_url, sonarr_key, sonarr_series_id)
        if not series_data:
            raise ValueError(f"Series ID {sonarr_series_id} not found in Sonarr.")

        episodes_data = await SonarrClient.get_episodes(sonarr_url, sonarr_key, sonarr_series_id)

        # 2. Create or update Show
        stmt = select(Show).where(Show.sonarr_series_id == sonarr_series_id)
        res = await db.execute(stmt)
        show = res.scalars().first()

        title = series_data.get("title", f"Show {sonarr_series_id}")
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

        # 3. Create or update canonical Episodes
        episodes_map: Dict[int, Episode] = {}
        for ep_data in episodes_data:
            s_ep_id = ep_data.get("id")
            stmt_ep = select(Episode).where(Episode.show_id == show.id, Episode.sonarr_episode_id == s_ep_id)
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

            # Save Sonarr variation entry
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

        # 4. Fetch External Sources
        all_tmdb_episodes: List[Dict[str, Any]] = []
        tmdb_key = config.get("tmdb_api_key")
        if tmdb_key:
            try:
                if not show.tmdb_id and show.tvdb_id:
                    show.tmdb_id = await TMDBClient.find_by_external_id(tmdb_key, str(show.tvdb_id), "tvdb_id")
                if show.tmdb_id:
                    seasons = set(ep.season_number for ep in episodes_map.values())
                    for s in sorted(seasons):
                        eps = await TMDBClient.get_season_episodes(tmdb_key, show.tmdb_id, s)
                        for t_ep in eps:
                            all_tmdb_episodes.append({"id": str(t_ep.get("id")), "season": t_ep.get("season_number"), "episode": t_ep.get("episode_number"), "title": t_ep.get("name"), "overview": t_ep.get("overview"), "air_date": t_ep.get("air_date"), "raw": t_ep})
            except Exception as e: await log(f"TMDB fetch error: {str(e)}")

        all_tvmaze_episodes: List[Dict[str, Any]] = []
        try:
            tvmaze_show = await TVmazeClient.lookup_show(tvdb_id=show.tvdb_id, imdb_id=show.imdb_id, title=show.title)
            if tvmaze_show:
                show.tvmaze_id = tvmaze_show.get("id")
                raw_tvmaze = await TVmazeClient.get_episodes(show.tvmaze_id)
                for tv_ep in raw_tvmaze:
                    all_tvmaze_episodes.append({"id": str(tv_ep.get("id")), "season": tv_ep.get("season"), "episode": tv_ep.get("number"), "title": tv_ep.get("name"), "overview": re.sub(r"<[^>]+>", "", tv_ep.get("summary") or ""), "air_date": tv_ep.get("airdate"), "raw": tv_ep})
        except Exception as e: await log(f"TVmaze fetch error: {str(e)}")

        all_omdb_episodes: List[Dict[str, Any]] = []
        omdb_key = config.get("omdb_api_key")
        if omdb_key and show.imdb_id:
            try:
                seasons = set(ep.season_number for ep in episodes_map.values() if ep.season_number > 0)
                for s in sorted(seasons):
                    omdb_eps = await OMDbClient.get_season_episodes(omdb_key, show.imdb_id, s)
                    for o_ep in omdb_eps:
                        all_omdb_episodes.append({"id": o_ep.get("imdbID"), "season": s, "episode": int(o_ep.get("Episode", 0)), "title": o_ep.get("Title"), "overview": o_ep.get("Plot"), "air_date": o_ep.get("Released"), "raw": o_ep})
                await log(f"Retrieved {len(all_omdb_episodes)} total OMDb episodes.")
            except Exception as e:
                await log(f"OMDb fetch error: {str(e)}")

        # 5. Multi-Stage Matching for Each Source
        ollama_url = config.get("ollama_url", "http://localhost:11434")
        primary_model = config.get("ollama_primary_model", "gemma4:e4b")
        fallback_model = config.get("ollama_fallback_model", "gemma4-obliterated:latest")
        canonical_list = list(episodes_map.values())

        if job:
            job.progress = 20.0
            await db.commit()

        # Match TMDB
        if all_tmdb_episodes:
            if job and concurrency_manager.is_cancelled(job.id):
                return show
            await cls.match_source_multistage(db=db, show=show, canonical_episodes=canonical_list, source_name="TMDB", source_episodes=all_tmdb_episodes, ollama_url=ollama_url, primary_model=primary_model, fallback_model=fallback_model, log_cb=log)

        if job:
            job.progress = 45.0
            await db.commit()

        # Match TVmaze
        if all_tvmaze_episodes:
            if job and concurrency_manager.is_cancelled(job.id):
                return show
            await cls.match_source_multistage(db=db, show=show, canonical_episodes=canonical_list, source_name="TVmaze", source_episodes=all_tvmaze_episodes, ollama_url=ollama_url, primary_model=primary_model, fallback_model=fallback_model, log_cb=log)

        if job:
            job.progress = 70.0
            await db.commit()

        # Match OMDb
        if all_omdb_episodes:
            if job and concurrency_manager.is_cancelled(job.id):
                return show
            await cls.match_source_multistage(db=db, show=show, canonical_episodes=canonical_list, source_name="OMDb", source_episodes=all_omdb_episodes, ollama_url=ollama_url, primary_model=primary_model, fallback_model=fallback_model, log_cb=log)

        # 6. Final Status Evaluation (Step 2C)
        available_sources_count = sum(1 for el in [all_tmdb_episodes, all_tvmaze_episodes, all_omdb_episodes] if len(el) > 0)
        
        for ep in canonical_list:
            stmt_m = select(EpisodeSourceMetadata).where(EpisodeSourceMetadata.episode_id == ep.id)
            res_m = await db.execute(stmt_m)
            metas = [m for m in res_m.scalars().all() if m.source_name != "sonarr"]

            matched_sources = sum(1 for m in metas if m.match_method in ("PERFECT_MATCH", "LOOSE_AI_CONFIRMED", "AI_SEARCH_CONFIRMED", "MANUAL_MATCH"))
            no_match_sources = sum(1 for m in metas if m.match_method == "NO_MATCH")

            if matched_sources >= available_sources_count and available_sources_count > 0:
                ep.ai_verification_status = "EXACT_MATCH"
                ep.ai_confidence_score = 100.0
                ep.ai_audit_notes = f"Verified across all {matched_sources} active external providers."
            elif (matched_sources + no_match_sources) >= available_sources_count and available_sources_count > 0:
                ep.ai_verification_status = "AI_MATCHED"
                ep.ai_confidence_score = 95.0
                ep.ai_audit_notes = f"Matched across {matched_sources} providers ({no_match_sources} confirmed absent)."
            elif matched_sources > 0:
                ep.ai_verification_status = "REQUIRES_USER_INTERVENTION"
                ep.ai_confidence_score = 65.0
                ep.ai_audit_notes = f"Partially matched ({matched_sources}/{available_sources_count} sources). Requires review."
            else:
                ep.ai_verification_status = "REQUIRES_USER_INTERVENTION"
                ep.ai_confidence_score = 30.0
                ep.ai_audit_notes = "No external match confirmed. Requires manual audit."

        await db.commit()
        await log("Completed multi-stage show matching pass.")
        return show
