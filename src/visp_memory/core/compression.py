"""
Memory Compression System

Mimics how human memory compresses over time:
- Recent events: detailed episodic memories
- Older events: compressed into semantic knowledge
- Very old: only essential patterns remain

Compression can use an LLM for intelligent summarization,
or fall back to heuristic-based compression.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from visp_memory.core.clock import utc_now
from visp_memory.core.ranking import projected_importance
from visp_memory.core.storage import BaseStorage
from visp_memory.core.tokens import compute_savings
from visp_memory.core.trust import WriteChannel, channel_policy, with_channel_provenance

COMPRESSION_PROMPT_HEADER = (
    "Compress these {count} related memories into a single piece of actionable "
    "knowledge.\n"
    "Focus on patterns, rules, or insights that would help future decision-making.\n"
    "Be concise but preserve essential information."
)


class MemoryCompressor:
    """
    Compresses episodic memories into semantic knowledge.

    The compression process:
    1. Group related episodic memories (by category, tags, content similarity)
    2. Extract patterns and generalizations
    3. Create semantic memories from the patterns
    4. Mark episodic memories as compressed (but don't delete)
    """

    def __init__(
        self, storage: BaseStorage, llm_compress_fn: Optional[Callable[[List[str]], str]] = None
    ):
        """
        Initialize compressor.

        Args:
            storage: Storage instance
            llm_compress_fn: Optional function that takes a list of memory
                           contents and returns a compressed summary.
                           If None, uses heuristic compression.
        """
        self.storage = storage
        self._llm_compress = llm_compress_fn

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string, handling both timezone-aware and naive formats.

        Returns None when the input is falsy (missing/None/empty) or otherwise
        unparseable, so callers can skip rows that can't be dated instead of
        crashing.
        """
        from datetime import timezone

        if not dt_str:
            return None

        try:
            # Remove 'Z' suffix and parse
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        # If naive, make it UTC-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def compress_episodes_to_semantic(
        self, episodes: List[Dict[str, Any]], category: str = None
    ) -> Optional[str]:
        """
        Compress a group of episodes into semantic knowledge.

        Args:
            episodes: List of episodic memories to compress
            category: Category for the resulting semantic memory

        Returns:
            ID of created semantic memory, or None if compression failed
        """
        if not episodes:
            return None

        contents = [ep["content"] for ep in episodes]
        source_ids = [ep["id"] for ep in episodes]

        # Calculate importance (average + boost for count)
        avg_importance = sum(ep.get("importance", 0.5) for ep in episodes) / len(episodes)
        importance = min(1.0, avg_importance + 0.1 * len(episodes) / 10)

        # Compress using LLM or heuristics
        if self._llm_compress:
            compressed = self._llm_compress(contents)
        else:
            compressed = self._heuristic_compress(contents)

        if not compressed:
            return None

        # Determine category if not provided
        if not category:
            categories = [ep.get("category") for ep in episodes if ep.get("category")]
            category = max(set(categories), key=categories.count) if categories else "pattern"

        # Collect tags
        all_tags = set()
        for ep in episodes:
            all_tags.update(ep.get("tags", []))
        all_tags.add("compressed")

        # Measure the token savings of this consolidation: many detailed episodic
        # memories collapse into one compact semantic memory. This is an auditable,
        # source-grounded figure (we hold both the originals and the result), so it
        # is recorded on the memory for later token-efficiency reporting.
        savings = compute_savings(contents, compressed)

        # Store semantic memory
        write_channel = WriteChannel.COMPRESSION
        policy = channel_policy(write_channel)
        semantic_id = self.storage.store_memory(
            content=compressed,
            layer="semantic",
            category=category,
            importance=importance,
            tags=with_channel_provenance(all_tags, write_channel),
            metadata={
                "compressed_from": len(episodes),
                "compressed_at": utc_now().isoformat(),
                "token_savings": savings.as_dict(),
                "write_channel": write_channel.value,
            },
            source_ids=source_ids,
            source=policy.source,
        )

        # Mark episodes as compressed
        for ep in episodes:
            self.storage.update_memory(
                ep["id"],
                metadata={
                    **(ep.get("metadata") or {}),
                    "compressed_to": semantic_id,
                    "compressed_at": utc_now().isoformat(),
                },
            )

        return semantic_id

    def _heuristic_compress(self, contents: List[str]) -> str:
        """
        Compress memories using heuristics (no LLM).

        Simple approach:
        - Find common patterns/words
        - Create a summary sentence
        """
        if len(contents) == 1:
            return f"Pattern observed: {contents[0]}"

        # Extract common words (simple approach)
        all_words = []
        for content in contents:
            words = content.lower().split()
            all_words.extend(words)

        # Find frequent words (excluding common ones)
        stopwords = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "must",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "under",
            "again",
            "further",
            "then",
            "once",
            "and",
            "but",
            "or",
            "nor",
            "so",
            "yet",
            "both",
            "each",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "not",
            "only",
            "own",
            "same",
            "than",
            "too",
            "very",
            "just",
            "also",
            "now",
            "here",
            "there",
            "when",
            "where",
            "why",
            "how",
            "all",
            "any",
            "this",
            "that",
            "these",
            "those",
            "i",
            "you",
            "he",
            "she",
            "it",
            "we",
            "they",
        }

        word_counts = defaultdict(int)
        for word in all_words:
            if word not in stopwords and len(word) > 2:
                word_counts[word] += 1

        # Get top keywords
        top_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        keywords = [w[0] for w in top_words]

        # Create summary
        if keywords:
            return f"Pattern ({len(contents)} instances): Related to {', '.join(keywords)}"
        else:
            return f"Pattern ({len(contents)} instances): {contents[0][:100]}..."

    def compress_semantic_to_principle(self, memories: List[Dict[str, Any]]) -> Optional[str]:
        """
        Level 2 Compression: Semantic Knowledge -> Higher-Level Principles.

        Args:
            memories: List of semantic memories to compress

        Returns:
            ID of created principle memory
        """
        if not memories or len(memories) < 3:
            return None

        contents = [m["content"] for m in memories]
        source_ids = [m["id"] for m in memories]

        # Calculate importance (higher for principles)
        avg_importance = sum(m.get("importance", 0.5) for m in memories) / len(memories)
        importance = min(1.0, avg_importance + 0.2)

        # Compress
        prompt_suffix = (
            "\n\nExtract the underlying universal principle or rule that explains these facts."
        )
        if self._llm_compress:
            # We wrap the underlying compress fn to add specific instruction
            # This is a bit hacky but works without changing the interface
            raw_compress = self._llm_compress
            self._llm_compress = lambda c: raw_compress(c + [prompt_suffix])
            try:
                compressed = self._llm_compress(contents)
            finally:
                self._llm_compress = raw_compress
        else:
            compressed = (
                f"Principle derived from {len(memories)} facts: "
                + self._heuristic_compress(contents)
            )

        if not compressed:
            return None

        savings = compute_savings(contents, compressed)

        # Store principle
        write_channel = WriteChannel.COMPRESSION
        policy = channel_policy(write_channel)
        principle_id = self.storage.store_memory(
            content=compressed,
            layer="semantic",
            category="principle",
            importance=importance,
            tags=with_channel_provenance(
                ["compressed", "principle"], write_channel
            ),
            metadata={
                "compressed_from": len(memories),
                "level": 2,
                "compressed_at": utc_now().isoformat(),
                "token_savings": savings.as_dict(),
                "write_channel": write_channel.value,
            },
            source_ids=source_ids,
            source=policy.source,
        )

        # Link source memories to this principle (don't mark as compressed/hidden,
        # as semantic memories are still valid on their own)
        for mem in memories:
            self.storage.add_relationship(
                source_id=mem["id"],
                target_id=principle_id,
                relationship="supports_principle",
                strength=0.9,
            )

        return principle_id

    def auto_compress(
        self, min_episodes: int = 5, category_threshold: int = 3, age_days: int = 7
    ) -> List[str]:
        """
        Run hierarchical compression.

        1. Episodic -> Semantic (Level 1)
        2. Semantic -> Principle (Level 2)
        """
        created = []

        # --- Level 1: Episodic -> Semantic ---

        # Get uncompressed episodes
        episodes = self.storage.list_memories(
            layer="episodic", limit=500, order_by="created_at ASC"
        )

        # Filter to old episodes not yet compressed
        from datetime import timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=age_days)

        def _is_old_uncompressed(ep: Dict[str, Any]) -> bool:
            if (ep.get("metadata") or {}).get("compressed_to"):
                return False
            created = self._parse_datetime(ep.get("created_at"))
            # Skip episodes we can't date rather than crashing on them.
            return created is not None and created < cutoff

        old_episodes = [ep for ep in episodes if _is_old_uncompressed(ep)]

        if len(old_episodes) >= min_episodes:
            # Group by category
            by_category = defaultdict(list)
            for ep in old_episodes:
                by_category[ep.get("category", "general")].append(ep)

            # Compress categories
            for category, cat_episodes in by_category.items():
                if len(cat_episodes) >= category_threshold:
                    semantic_id = self.compress_episodes_to_semantic(
                        cat_episodes, category=category
                    )
                    if semantic_id:
                        created.append(semantic_id)

        # --- Level 2: Semantic -> Principle ---

        # Get all semantic memories (excluding principles)
        semantic = self.storage.list_memories(layer="semantic", limit=1000)
        facts = [
            m
            for m in semantic
            if m.get("category") != "principle" and (m.get("metadata") or {}).get("level", 1) == 1
        ]

        # Cluster them (naive approach: group by auto-extracted topics/tags would be better)
        # For now, we'll try to group by category/tags
        by_category = defaultdict(list)
        for m in facts:
            by_category[m.get("category", "general")].append(m)

        for _category, items in by_category.items():
            if len(items) >= 5:  # Need more evidence for a principle
                # Only check items not already supporting a principle to avoid loops
                # (Ideally we checks relationships, but simplified for MVP)
                principle_id = self.compress_semantic_to_principle(items)
                if principle_id:
                    created.append(principle_id)

        return created

    def decay_old_memories(self, halflife_days: int = 30, min_importance: float = 0.1) -> int:
        """
        Decay importance of old, rarely-accessed memories.

        Mimics how human memories fade if not reinforced. The decay half-life is
        stretched by how often a memory has been recalled (spaced repetition), so
        frequently-used memories fade far more slowly than one-off notes.

        Args:
            halflife_days: Base days for importance to halve (before use-based stretch)
            min_importance: Floor for importance decay

        Returns:
            Number of memories decayed
        """
        decayed = 0

        # Get all memories
        for layer in ["episodic", "semantic"]:
            memories = self.storage.list_memories(layer=layer, limit=1000)

            for mem in memories:
                # Calculate age since last access. Coalesce explicitly: a row
                # may carry accessed_at=None (key present but null), in which
                # case dict.get would return None instead of the created_at
                # fallback.
                accessed = self._parse_datetime(
                    mem.get("accessed_at") or mem.get("created_at")
                )
                from datetime import timezone

                # Skip rows we can't date rather than crashing on them.
                if accessed is None:
                    continue

                age_days = (datetime.now(timezone.utc) - accessed).days

                if age_days < 1:
                    continue

                # Null-safe: some backends/legacy rows may omit importance.
                try:
                    current = float(mem.get("importance", 0.5))
                except (TypeError, ValueError):
                    current = 0.5

                # Use-aware exponential decay: more recalls -> slower forgetting.
                new_importance = projected_importance(
                    importance=current,
                    age_days=age_days,
                    halflife_days=halflife_days,
                    access_count=mem.get("access_count", 0),
                    min_importance=min_importance,
                )

                # Only update if significant change
                if current - new_importance > 0.05:
                    self.storage.update_memory(mem["id"], importance=new_importance)
                    decayed += 1

        return decayed


def create_llm_compressor(provider: str, model: str = None, api_key: str = None):
    """
    Create an LLM-based compression function.

    Args:
        provider: "openai", "anthropic", or "ollama"
        model: Model name (uses default if not specified)
        api_key: API key (uses env var if not specified)

    Returns:
        Compression function that takes List[str] and returns str
    """
    if provider == "openai":
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai required: pip install openai")

        client = OpenAI(api_key=api_key) if api_key else OpenAI()
        model = model or "gpt-4o-mini"

        def compress_fn(contents: List[str]) -> str:
            prompt = f"""{COMPRESSION_PROMPT_HEADER.format(count=len(contents))}

Memories:
{chr(10).join(f"- {c}" for c in contents)}

Compressed knowledge (1-2 sentences):"""

            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()

        return compress_fn

    elif provider == "anthropic":
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("anthropic required: pip install anthropic")

        client = Anthropic(api_key=api_key) if api_key else Anthropic()
        model = model or "claude-3-haiku-20240307"

        def compress_fn(contents: List[str]) -> str:
            prompt = f"""{COMPRESSION_PROMPT_HEADER.format(count=len(contents))}

Memories:
{chr(10).join(f"- {c}" for c in contents)}

Compressed knowledge (1-2 sentences):"""

            response = client.messages.create(
                model=model, max_tokens=200, messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()

        return compress_fn

    elif provider == "ollama":
        try:
            import ollama
        except ImportError:
            raise ImportError("ollama required: pip install ollama")

        model = model or "llama3.2"

        def compress_fn(contents: List[str]) -> str:
            prompt = f"""{COMPRESSION_PROMPT_HEADER.format(count=len(contents))}

Memories:
{chr(10).join(f"- {c}" for c in contents)}

Compressed knowledge (1-2 sentences):"""

            response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
            return response["message"]["content"].strip()

        return compress_fn

    else:
        raise ValueError(f"Unknown provider: {provider}")
