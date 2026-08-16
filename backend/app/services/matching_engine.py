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

        # 2. Query Fallback Model (first available fallback model)
        fb_to_use = fallback_model[0] if isinstance(fallback_model, list) and fallback_model else fallback_model
        if not fb_to_use or fb_to_use == primary_model:
            return matches_p

        resp_f, _ = await OllamaClient.query_with_retry_and_fallback(
            base_url=ollama_url,
            primary_model=fb_to_use,
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
        fallback_model: Optional[Any],
        show_title: str,
        source_name: str,
        batch_pairs: List[Tuple[Episode, Dict[str, Any]]]
    ) -> Tuple[Set[int], str]:
        """
        Step 1A/1B/2B: Batch High-Context Confirmation Prompt
        Prompts LLM to confirm comparisons with 'yes', or output comma-separated failed SxxEyy.
        If primary model does not confirm all items, iterates through fallback models on the remaining unconfirmed items.
        Returns: (confirmed_canonical_ids: Set[int], model_used: str)
        """
        if not batch_pairs:
            return set(), "NO_PAIRS"
        if not ollama_url:
            return set(ep.id for ep, _ in batch_pairs), "OLLAMA_DISABLED"

        fallbacks: List[str] = []
        if isinstance(fallback_model, list):
            fallbacks = [str(m).strip() for m in fallback_model if str(m).strip()]
        elif isinstance(fallback_model, str) and fallback_model.strip():
            fallbacks = [fallback_model.strip()]

        async def _query_batch_single_model(model_name: str, pairs: List[Tuple[Episode, Dict[str, Any]]]) -> Tuple[Set[int], bool]:
            if not pairs:
                return set(), True

            items = []
            for idx, (s_ep, cand) in enumerate(pairs, 1):
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
                f"here are the {len(pairs)} proposed episode comparisons:\n\n"
                f"{items_str}\n\n"
                f"please answer with \"yes\" if all {len(pairs)} episodes are valid matches. If any are NOT matches, answer with a comma-separated list of the failed {source_name} episode numbers in SxxEyy format (e.g. S01E03, S01E08)."
            )

            system_prompt = (
                "You are an expert TV episode matching engine. Confirm if the proposed episodes represent the exact same story. "
                "Answer 'yes' if all match, or output a comma-separated list of failed SxxEyy episodes."
            )

            resp_text = None
            for attempt in range(2):
                try:
                    out = await OllamaClient.query_model_text(
                        base_url=ollama_url,
                        model=model_name,
                        user_prompt=prompt,
                        system_prompt=system_prompt,
                        timeout=90.0
                    )
                    if out and out.strip():
                        resp_text = out
                        break
                except Exception:
                    pass

            if not resp_text:
                return set(), False

            clean = resp_text.strip().lower()
            failed_ses = set((int(s), int(e)) for s, e in re.findall(r"S(\d+)E(\d+)", resp_text, re.IGNORECASE))

            confirmed: Set[int] = set()
            if not failed_ses and (clean.startswith("yes") or "yes" in clean.split() or clean == "true"):
                for s_ep, _ in pairs:
                    confirmed.add(s_ep.id)
                return confirmed, True
            elif failed_ses:
                for s_ep, cand in pairs:
                    cs = cand.get("season") if cand.get("season") is not None else 0
                    ce = cand.get("episode") if cand.get("episode") is not None else 0
                    if (cs, ce) not in failed_ses:
                        confirmed.add(s_ep.id)
                return confirmed, len(confirmed) == len(pairs)
            elif clean.startswith("yes"):
                for s_ep, _ in pairs:
                    confirmed.add(s_ep.id)
                return confirmed, True

            return confirmed, False

        # 1. First query primary model
        confirmed_ids, all_confirmed = await _query_batch_single_model(primary_model, batch_pairs)
        models_used = [primary_model]

        # 2. If primary did not confirm all items, iterate through fallback models on remaining unconfirmed pairs
        if not all_confirmed and fallbacks:
            for fb in fallbacks:
                if not fb or fb == primary_model:
                    continue
                remaining_pairs = [p for p in batch_pairs if p[0].id not in confirmed_ids]
                if not remaining_pairs:
                    break
                fb_confirmed, _ = await _query_batch_single_model(fb, remaining_pairs)
                if fb_confirmed:
                    confirmed_ids.update(fb_confirmed)
                    models_used.append(fb)
                if len(confirmed_ids) == len(batch_pairs):
                    break

        return confirmed_ids, "+".join(models_used)

    @staticmethod
    async def ai_search_candidates(
        ollama_url: str,
        primary_model: str,
        fallback_model: Optional[Any],
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
        sonarr_desc = (canonical_ep.overview or "")[:200]
        sonarr_date = canonical_ep.air_date or "N/A"

        cand_lines = []
        for c in candidates:
            cs = c.get("season") if c.get("season") is not None else 0
            ce = c.get("episode") if c.get("episode") is not None else 0
            ct = c.get("title") or ""
            cd = (c.get("overview") or "")[:120]
            cdate = c.get("air_date") or "N/A"
            cand_lines.append(
                f"        {source_name} S{cs:02d}E{ce:02d}\n"
                f"        match title: {ct}\n"
                f"        match description: {cd}\n"
                f"        air date: {cdate}"
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
        fallback_model: Optional[Any],
        batch_size: int = 10,
        progress_range: Tuple[float, float] = (0.0, 100.0),
        progress_cb: Optional[Callable[[float, str], Awaitable[None]]] = None,
        log_cb: Optional[Callable[[str], Awaitable[None]]] = None
    ):
        """
        Executes Step 0, Step 1A, Step 1B, Step 2A, Step 2B, Step 2C for a single source with granular progress updates and configurable batch size.
        """
        from backend.app.services.concurrency_manager import concurrency_manager

        p_start, p_end = progress_range
        batch_size = max(1, batch_size)

        async def log(msg: str):
            if log_cb:
                await log_cb(msg)

        async def update_progress(fraction: float, msg: str):
            pct = round(p_start + fraction * (p_end - p_start), 1)
            if progress_cb:
                await progress_cb(pct, msg)
            if log_cb:
                await log_cb(f"[{source_name} ({pct}%)] {msg}")

        if not source_episodes:
            await log(f"[{source_name}] No external episodes available in source index.")
            return

        mapped_source_keys: Set[Tuple[int, int]] = set()
        mapped_canonical_ids: Set[int] = set()

        # Step 0: AI No Match Marking (0% -> 10% of source allocation)
        await update_progress(0.02, f"Step 0: Checking for non-existent episodes ({len(source_episodes)} {source_name} vs {len(canonical_episodes)} Sonarr)...")
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
            se_key = (ep.season_number, ep.episode_number)
            if se_key in no_match_pairs:
                mapped_canonical_ids.add(ep.id)
                meta = EpisodeSourceMetadata(
                    episode_id=ep.id,
                    show_id=show.id,
                    source_name=source_name.lower(),
                    source_show_id=str(show.tmdb_id if source_name == 'TMDB' else (show.tvmaze_id if source_name == 'TVmaze' else show.imdb_id)),
                    source_episode_id=None,
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
            await log(f"[{source_name}] Step 0: Marked {len(no_match_pairs)} episodes as confirmed absent (NO_MATCH).")
        await update_progress(0.10, f"Step 0 Complete: {len(no_match_pairs)} episodes marked NO_MATCH.")

        # Step 1A: Strict 1:1 Title Matching (10% -> 40% of source allocation)
        await update_progress(0.12, "Step 1A: Scanning entire source index for 1:1 exact title matches...")
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

        total_1a_batches = max(1, (len(proposed_1a) + batch_size - 1) // batch_size)
        await log(f"[{source_name}] Step 1A: Found {len(proposed_1a)} strict 1:1 candidates. Confirming in {total_1a_batches} batches of {batch_size}...")

        for b_idx, i in enumerate(range(0, len(proposed_1a), batch_size), 1):
            batch = proposed_1a[i:i+batch_size]
            first_ep = batch[0][0]
            last_ep = batch[-1][0]
            frac = 0.10 + 0.30 * (b_idx / total_1a_batches)
            await update_progress(frac, f"Step 1A: Batch {b_idx}/{total_1a_batches} (S{first_ep.season_number}E{first_ep.episode_number} - S{last_ep.season_number}E{last_ep.episode_number})")

            confirmed_ids, model_used = await cls.ai_batch_confirm_matches(
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model,
                show_title=show.title,
                source_name=source_name,
                batch_pairs=batch
            )
            n_conf = 0
            for ep, cand in batch:
                if ep.id in confirmed_ids:
                    n_conf += 1
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

            await log(f"[{source_name}] Step 1A: Batch {b_idx}/{total_1a_batches} -> AI confirmed {n_conf}/{len(batch)} matches ({model_used}).")

        await db.commit()
        await update_progress(0.40, f"Step 1A Complete: {len(mapped_canonical_ids)} strict matches saved.")

        # Step 1B: Loose Title Matching (40% -> 60% of source allocation)
        await update_progress(0.42, "Step 1B: Scanning remaining unmapped episodes for loose / parenthetical matches...")
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

        total_1b_batches = max(1, (len(proposed_1b) + batch_size - 1) // batch_size)
        await log(f"[{source_name}] Step 1B: Found {len(proposed_1b)} loose candidate matches. Confirming in {total_1b_batches} batches...")

        for b_idx, i in enumerate(range(0, len(proposed_1b), batch_size), 1):
            batch = proposed_1b[i:i+batch_size]
            frac = 0.40 + 0.20 * (b_idx / total_1b_batches)
            await update_progress(frac, f"Step 1B: Batch {b_idx}/{total_1b_batches}")

            confirmed_ids, model_used = await cls.ai_batch_confirm_matches(
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_model,
                show_title=show.title,
                source_name=source_name,
                batch_pairs=batch
            )
            n_conf = 0
            for ep, cand in batch:
                if ep.id in confirmed_ids:
                    n_conf += 1
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

            await log(f"[{source_name}] Step 1B: Batch {b_idx}/{total_1b_batches} -> AI confirmed {n_conf}/{len(batch)} loose matches ({model_used}).")

        await db.commit()
        await update_progress(0.60, f"Step 1B Complete: {len(mapped_canonical_ids)} total matches saved.")

        # Step 2A & 2B: AI Candidate Search & Batch Confirmation (60% -> 100% of source allocation)
        remaining_unmapped_sonarr = [ep for ep in canonical_episodes if ep.id not in mapped_canonical_ids]
        if remaining_unmapped_sonarr:
            await update_progress(0.62, f"Step 2A: Running LLM candidate search across {len(remaining_unmapped_sonarr)} unmapped episodes...")
            remaining_pool = [c for c in source_episodes if (c.get("season") or 0, c.get("episode") or 0) not in mapped_source_keys]
            source_index = SourceIndex(source_name, remaining_pool)
            proposed_2a: List[Tuple[Episode, Dict[str, Any]]] = []

            for ep_idx, ep in enumerate(remaining_unmapped_sonarr, 1):
                frac = 0.60 + 0.25 * (ep_idx / len(remaining_unmapped_sonarr))
                await update_progress(frac, f"Step 2A: Searching ep {ep_idx}/{len(remaining_unmapped_sonarr)}: S{ep.season_number}E{ep.episode_number} '{ep.title}'")

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
                        await log(f"[{source_name}] Step 2A: S{ep.season_number}E{ep.episode_number} -> Suggested S{s_num}E{e_num} '{cand.get('title')}'")

            # Batch confirm Step 2A proposals (Step 2B)
            if proposed_2a:
                total_2b_batches = max(1, (len(proposed_2a) + batch_size - 1) // batch_size)
                await log(f"[{source_name}] Step 2B: Confirming {len(proposed_2a)} search proposals in {total_2b_batches} batches of {batch_size}...")

                for b_idx, i in enumerate(range(0, len(proposed_2a), batch_size), 1):
                    batch = proposed_2a[i:i+batch_size]
                    frac = 0.85 + 0.15 * (b_idx / total_2b_batches)
                    await update_progress(frac, f"Step 2B: Confirming search batch {b_idx}/{total_2b_batches}")

                    confirmed_ids, model_used = await cls.ai_batch_confirm_matches(
                        ollama_url=ollama_url,
                        primary_model=primary_model,
                        fallback_model=fallback_model,
                        show_title=show.title,
                        source_name=source_name,
                        batch_pairs=batch
                    )
                    n_conf = 0
                    for ep, cand in batch:
                        if ep.id in confirmed_ids:
                            n_conf += 1
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

                    await log(f"[{source_name}] Step 2B: Batch {b_idx}/{total_2b_batches} -> AI confirmed {n_conf}/{len(batch)} proposals ({model_used}).")

                await db.commit()

        unmapped_final = len(canonical_episodes) - len(mapped_canonical_ids)
        await update_progress(1.0, f"Completed: {len(mapped_canonical_ids)}/{len(canonical_episodes)} mapped ({unmapped_final} unmapped).")

    @classmethod
    async def process_show_ingestion(
        cls,
        db: AsyncSession,
        sonarr_series_id: int,
        config: Dict[str, Any],
        job: Optional[Job] = None,
        scan_mode: str = "full",
        selected_sources: Optional[List[str]] = None,
        target_season_number: Optional[int] = None,
        target_episode_id: Optional[int] = None,
    ) -> Show:
        """
        Full show ingestion orchestrator executing the Multi-Stage Batch & Search Matching Pipeline with continuous live progress tracking.
        Supports full scan, no scan, custom sources, and targeted season/episode rescans.
        """
        from backend.app.services.concurrency_manager import concurrency_manager

        async def log(msg: str):
            if job:
                job.logs = (job.logs or "") + f"\n{msg}"
                try:
                    await db.commit()
                except Exception:
                    pass

        async def update_job_progress(progress_val: float, message_val: str):
            if job:
                job.progress = progress_val
                job.message = message_val[:250]
                try:
                    await db.commit()
                except Exception:
                    pass

        target_desc = ""
        if target_episode_id is not None:
            target_desc = f" (Episode ID: {target_episode_id})"
        elif target_season_number is not None:
            target_desc = f" (Season: {target_season_number})"

        await update_job_progress(2.0, f"Fetching show metadata from Sonarr (Series ID: {sonarr_series_id}){target_desc}...")

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

        await update_job_progress(5.0, f"Initialized show '{show.title}' with {len(episodes_data)} canonical episodes.")

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

        # Check if scan_mode is 'none'
        canonical_list = list(episodes_map.values())
        if target_season_number is not None:
            canonical_list = [ep for ep in canonical_list if ep.season_number == target_season_number]
        if target_episode_id is not None:
            canonical_list = [ep for ep in canonical_list if ep.id == target_episode_id]

        if scan_mode == "none" or not canonical_list:
            await update_job_progress(100.0, "Show metadata synced from Sonarr. External AI scan skipped per options.")
            return show

        await update_job_progress(8.0, "Fetching external repositories (TMDB, TVmaze, OMDb)...")

        # 4. Fetch External Sources
        all_tmdb_episodes: List[Dict[str, Any]] = []
        tmdb_key = config.get("tmdb_api_key")
        if tmdb_key:
            try:
                if not show.tmdb_id and show.tvdb_id:
                    show.tmdb_id = await TMDBClient.find_by_external_id(tmdb_key, str(show.tvdb_id), "tvdb_id")
                if show.tmdb_id:
                    seasons = set(ep.season_number for ep in canonical_list)
                    for s in sorted(seasons):
                        eps = await TMDBClient.get_season_episodes(tmdb_key, show.tmdb_id, s)
                        for t_ep in eps:
                            all_tmdb_episodes.append({"id": str(t_ep.get("id")), "season": t_ep.get("season_number"), "episode": t_ep.get("episode_number"), "title": t_ep.get("name"), "overview": t_ep.get("overview"), "air_date": t_ep.get("air_date"), "raw": t_ep})
                    await log(f"Retrieved {len(all_tmdb_episodes)} total TMDB episodes.")
            except Exception as e: await log(f"TMDB fetch error: {str(e)}")

        all_tvmaze_episodes: List[Dict[str, Any]] = []
        try:
            tvmaze_show = await TVmazeClient.lookup_show(tvdb_id=show.tvdb_id, imdb_id=show.imdb_id, title=show.title)
            if tvmaze_show:
                show.tvmaze_id = tvmaze_show.get("id")
                raw_tvmaze = await TVmazeClient.get_episodes(show.tvmaze_id)
                for tv_ep in raw_tvmaze:
                    all_tvmaze_episodes.append({"id": str(tv_ep.get("id")), "season": tv_ep.get("season"), "episode": tv_ep.get("number"), "title": tv_ep.get("name"), "overview": re.sub(r"<[^>]+>", "", tv_ep.get("summary") or ""), "air_date": tv_ep.get("airdate"), "raw": tv_ep})
                await log(f"Retrieved {len(all_tvmaze_episodes)} total TVmaze episodes.")
        except Exception as e: await log(f"TVmaze fetch error: {str(e)}")

        all_omdb_episodes: List[Dict[str, Any]] = []
        omdb_key = config.get("omdb_api_key")
        if omdb_key and show.imdb_id:
            try:
                seasons = set(ep.season_number for ep in canonical_list if ep.season_number > 0)
                for s in sorted(seasons):
                    omdb_eps = await OMDbClient.get_season_episodes(omdb_key, show.imdb_id, s)
                    for o_ep in omdb_eps:
                        all_omdb_episodes.append({"id": o_ep.get("imdbID"), "season": s, "episode": int(o_ep.get("Episode", 0)), "title": o_ep.get("Title"), "overview": o_ep.get("Plot"), "air_date": o_ep.get("Released"), "raw": o_ep})
                await log(f"Retrieved {len(all_omdb_episodes)} total OMDb episodes.")
            except Exception as e:
                await log(f"OMDb fetch error: {str(e)}")

        # 5. Multi-Stage Matching for Selected Sources with Distributed Progress
        ollama_url = config.get("ollama_url", "http://localhost:11434")
        primary_model = config.get("ollama_primary_model", "gemma4:e2b")
        
        fallback_models = config.get("ollama_fallback_models")
        if not fallback_models or not isinstance(fallback_models, list):
            if config.get("ollama_fallback_model"):
                fallback_models = [config.get("ollama_fallback_model")]
            else:
                fallback_models = ["Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M"]

        batch_size = int(config.get("ai_batch_size", 10)) if str(config.get("ai_batch_size", "")).isdigit() else 10
        batch_size = max(1, batch_size)

        norm_sources = [s.lower().strip() for s in (selected_sources or ["tmdb", "tvmaze", "omdb"])]

        active_sources = []
        if all_tmdb_episodes and "tmdb" in norm_sources:
            active_sources.append(("TMDB", all_tmdb_episodes))
        if all_tvmaze_episodes and "tvmaze" in norm_sources:
            active_sources.append(("TVmaze", all_tvmaze_episodes))
        if all_omdb_episodes and "omdb" in norm_sources:
            active_sources.append(("OMDb", all_omdb_episodes))

        start_pct = 10.0
        end_pct = 92.0
        total_range = end_pct - start_pct
        source_slice = total_range / max(1, len(active_sources))

        for idx, (s_name, s_eps) in enumerate(active_sources):
            if job and concurrency_manager.is_cancelled(job.id):
                return show

            s_p_start = start_pct + idx * source_slice
            s_p_end = s_p_start + source_slice

            await cls.match_source_multistage(
                db=db,
                show=show,
                canonical_episodes=canonical_list,
                source_name=s_name,
                source_episodes=s_eps,
                ollama_url=ollama_url,
                primary_model=primary_model,
                fallback_model=fallback_models,
                batch_size=batch_size,
                progress_range=(s_p_start, s_p_end),
                progress_cb=update_job_progress,
                log_cb=log
            )

        # 6. Final Status Evaluation (Step 2C)
        await update_job_progress(95.0, "Auditing final multi-source consistency and integrity...")
        available_sources_count = len(active_sources)
        
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
        await update_job_progress(100.0, "Completed multi-stage show matching pass.")
        return show
