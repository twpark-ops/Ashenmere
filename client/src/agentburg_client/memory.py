"""Agent memory — persistence-backed memory with categories and importance scoring.

Stores experiences as categorized entries with importance scoring.
Supports in-memory operation (deque) with optional SQLite persistence via aiosqlite.
Retrieves relevant memories using keyword matching with recency and importance weighting.
"""

from __future__ import annotations

import logging
from collections import deque
from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

# SQLite schema for persistent memory storage
MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'observation',
    tick INTEGER NOT NULL DEFAULT 0,
    importance REAL NOT NULL DEFAULT 0.5,
    created_at REAL NOT NULL DEFAULT (unixepoch('now'))
);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_tick ON memories(tick DESC);
"""


class MemoryCategory(StrEnum):
    """Categories for classifying agent memories."""

    OBSERVATION = "observation"  # What the agent perceived in the world
    DECISION = "decision"  # Actions the agent chose and their outcomes
    INTERACTION = "interaction"  # Exchanges with other agents
    KNOWLEDGE = "knowledge"  # Learned facts about the world, market, etc.


class MemoryEntry(NamedTuple):
    """A single memory entry with metadata."""

    key: str
    value: str
    category: MemoryCategory
    tick: int
    importance: float


def compute_importance(
    text: str,
    category: MemoryCategory = MemoryCategory.OBSERVATION,
) -> float:
    """Heuristic importance scoring for a memory entry.

    Scores range from 0.0 (trivial) to 1.0 (critical).
    Uses keyword signals and category weighting.
    """
    score = 0.5  # Base importance

    # Category baseline adjustments
    category_weights: dict[MemoryCategory, float] = {
        MemoryCategory.OBSERVATION: 0.0,
        MemoryCategory.DECISION: 0.1,
        MemoryCategory.INTERACTION: 0.05,
        MemoryCategory.KNOWLEDGE: 0.15,
    }
    score += category_weights.get(category, 0.0)

    text_lower = text.lower()

    # High-importance keywords (financial outcomes, threats, opportunities)
    high_keywords = {
        "failed",
        "error",
        "bankrupt",
        "profit",
        "loss",
        "lawsuit",
        "sued",
        "critical",
        "warning",
        "opportunity",
        "deal",
        "rich",
        "poor",
        "reputation",
        "credit",
        "loan",
        "debt",
    }
    # Medium-importance keywords
    medium_keywords = {
        "buy",
        "sell",
        "trade",
        "invest",
        "business",
        "price",
        "market",
        "change",
        "new",
        "started",
    }

    for kw in high_keywords:
        if kw in text_lower:
            score += 0.1
    for kw in medium_keywords:
        if kw in text_lower:
            score += 0.03

    return min(1.0, max(0.0, score))


class Memory:
    """Agent memory with in-memory buffer and optional SQLite persistence.

    The in-memory deque provides fast access for the active session.
    SQLite persistence (when enabled) allows memories to survive restarts.

    Usage:
        # In-memory only (backward compatible)
        mem = Memory(max_size=500)
        mem.store("something happened")

        # With persistence
        mem = Memory(max_size=500, db_path="agent_memory.db")
        await mem.initialize()  # Create tables
        mem.store("something happened", category=MemoryCategory.OBSERVATION, tick=42)
        await mem.persist()  # Flush to disk
        await mem.load()  # Load from disk on restart
        await mem.close()  # Clean up
    """

    def __init__(self, max_size: int = 500, db_path: str = "") -> None:
        self._max_size = max_size
        self._memories: deque[MemoryEntry] = deque(maxlen=max_size)
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # --- Synchronous API (backward compatible) ---

    def store(
        self,
        memory: str,
        *,
        category: MemoryCategory = MemoryCategory.OBSERVATION,
        tick: int = 0,
        importance: float | None = None,
    ) -> None:
        """Store a new memory in the in-memory buffer.

        Args:
            memory: The text content of the memory.
            category: Classification of the memory.
            tick: The world tick when this memory was created.
            importance: Explicit importance score (0.0-1.0). If None, auto-computed.
        """
        if not memory:
            return

        # Deduplicate by value
        for existing in self._memories:
            if existing.value == memory:
                return

        if importance is None:
            importance = compute_importance(memory, category)
        importance = min(1.0, max(0.0, importance))

        # Use tick + hash as a unique key
        key = f"t{tick}_{hash(memory) & 0xFFFFFFFF:08x}"

        entry = MemoryEntry(
            key=key,
            value=memory,
            category=category,
            tick=tick,
            importance=importance,
        )
        self._memories.append(entry)

    def recall(self, context: str, limit: int = 5) -> list[str]:
        """Recall memories relevant to the given context.

        Uses keyword overlap + recency + importance weighting.
        Returns memory values (strings) for backward compatibility.
        """
        if not self._memories:
            return []

        context_words = set(context.lower().split())

        scored: list[tuple[float, MemoryEntry]] = []
        total = len(self._memories)
        for i, entry in enumerate(self._memories):
            mem_words = set(entry.value.lower().split())
            overlap = len(context_words & mem_words)
            # Recency bonus: newer memories get a small boost
            recency = i / total if total > 0 else 0.0
            # Combined score: keyword match + recency + importance
            score = overlap + recency * 0.5 + entry.importance * 1.5
            scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        return [entry.value for _, entry in scored[:limit]]

    def recall_entries(
        self,
        context: str,
        limit: int = 5,
        category: MemoryCategory | None = None,
    ) -> list[MemoryEntry]:
        """Recall full memory entries (with metadata) relevant to context.

        Args:
            context: Search context string for keyword matching.
            limit: Maximum number of entries to return.
            category: Optional filter by memory category.
        """
        if not self._memories:
            return []

        pool = self._memories
        if category is not None:
            pool = deque(e for e in self._memories if e.category == category)

        context_words = set(context.lower().split())
        scored: list[tuple[float, MemoryEntry]] = []
        total = len(pool)
        for i, entry in enumerate(pool):
            mem_words = set(entry.value.lower().split())
            overlap = len(context_words & mem_words)
            recency = i / total if total > 0 else 0.0
            score = overlap + recency * 0.5 + entry.importance * 1.5
            scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        return [entry for _, entry in scored[:limit]]

    def get_recent(self, limit: int = 10) -> list[str]:
        """Get the most recent memories (values only, backward compatible)."""
        return [entry.value for entry in list(self._memories)[-limit:]]

    def get_recent_entries(self, limit: int = 10) -> list[MemoryEntry]:
        """Get the most recent memory entries with full metadata."""
        return list(self._memories)[-limit:]

    def size(self) -> int:
        """Return the number of memories currently stored."""
        return len(self._memories)

    def clear(self) -> None:
        """Clear all in-memory memories."""
        self._memories.clear()

    def prune(self, keep: int | None = None) -> int:
        """Prune low-importance memories, keeping the top N by importance.

        Old, low-importance memories are discarded first.
        Returns the number of memories pruned.

        Args:
            keep: Number of memories to retain. Defaults to max_size * 0.8.
        """
        if keep is None:
            keep = int(self._max_size * 0.8)

        if len(self._memories) <= keep:
            return 0

        # Sort by combined score: importance + recency
        total = len(self._memories)
        scored = []
        for i, entry in enumerate(self._memories):
            recency = i / total if total > 0 else 0.0
            combined = entry.importance * 0.7 + recency * 0.3
            scored.append((combined, entry))

        scored.sort(key=lambda x: -x[0])
        survivors = [entry for _, entry in scored[:keep]]

        # Preserve original insertion order among survivors
        survivor_keys = {e.key for e in survivors}
        pruned_count = len(self._memories) - len(survivors)

        new_deque: deque[MemoryEntry] = deque(maxlen=self._max_size)
        for entry in self._memories:
            if entry.key in survivor_keys:
                new_deque.append(entry)
        self._memories = new_deque

        logger.debug("Pruned %d memories, kept %d", pruned_count, len(self._memories))
        return pruned_count

    # --- Async persistence API (SQLite via aiosqlite) ---

    async def initialize(self) -> None:
        """Initialize SQLite database and create schema if needed.

        No-op if db_path is empty (in-memory only mode).
        """
        if not self._db_path:
            logger.debug("No db_path configured, running in-memory only mode")
            return

        try:
            import aiosqlite
        except ImportError:
            logger.warning("aiosqlite not installed — memory persistence disabled. Install with: pip install aiosqlite")
            return

        try:
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.executescript(MEMORY_SCHEMA)
            await self._db.commit()
            logger.info("Memory database initialized at %s", self._db_path)
        except Exception:
            logger.exception("Failed to initialize memory database at %s", self._db_path)
            self._db = None

    async def persist(self) -> int:
        """Persist all in-memory entries to SQLite.

        Uses INSERT OR REPLACE to handle duplicates.
        Returns the number of entries written.
        """
        if self._db is None:
            return 0

        entries = list(self._memories)
        if not entries:
            return 0

        try:
            await self._db.executemany(
                """INSERT OR REPLACE INTO memories (key, value, category, tick, importance)
                   VALUES (?, ?, ?, ?, ?)""",
                [(e.key, e.value, e.category.value, e.tick, e.importance) for e in entries],
            )
            await self._db.commit()
            logger.debug("Persisted %d memories to database", len(entries))
            return len(entries)
        except Exception:
            logger.exception("Failed to persist memories")
            return 0

    async def load(self) -> int:
        """Load memories from SQLite into in-memory buffer.

        Loads the most recent memories up to max_size, ordered by tick then importance.
        Returns the number of entries loaded.
        """
        if self._db is None:
            return 0

        try:
            cursor = await self._db.execute(
                """SELECT key, value, category, tick, importance
                   FROM memories
                   ORDER BY tick DESC, importance DESC
                   LIMIT ?""",
                (self._max_size,),
            )
            rows = await cursor.fetchall()

            # Clear current buffer and load in chronological order (reverse of query)
            self._memories.clear()
            for key, value, category_str, tick, importance in reversed(rows):
                try:
                    cat = MemoryCategory(category_str)
                except ValueError:
                    cat = MemoryCategory.OBSERVATION
                entry = MemoryEntry(
                    key=key,
                    value=value,
                    category=cat,
                    tick=tick,
                    importance=importance,
                )
                self._memories.append(entry)

            logger.info("Loaded %d memories from database", len(rows))
            return len(rows)
        except Exception:
            logger.exception("Failed to load memories from database")
            return 0

    async def prune_db(self, keep: int | None = None) -> int:
        """Prune low-importance entries from the SQLite database.

        Keeps the top N entries by importance, removing the rest.
        Returns the number of entries pruned.
        """
        if self._db is None:
            return 0
        if keep is None:
            keep = int(self._max_size * 0.8)

        try:
            cursor = await self._db.execute("SELECT COUNT(*) FROM memories")
            row = await cursor.fetchone()
            total = row[0] if row else 0

            if total <= keep:
                return 0

            # Delete entries not in the top N by importance
            await self._db.execute(
                """DELETE FROM memories WHERE key NOT IN (
                       SELECT key FROM memories
                       ORDER BY importance DESC, tick DESC
                       LIMIT ?
                   )""",
                (keep,),
            )
            await self._db.commit()

            cursor = await self._db.execute("SELECT COUNT(*) FROM memories")
            row = await cursor.fetchone()
            new_total = row[0] if row else 0
            pruned = total - new_total

            logger.info("Pruned %d entries from database, %d remaining", pruned, new_total)
            return pruned
        except Exception:
            logger.exception("Failed to prune memory database")
            return 0

    async def close(self) -> None:
        """Persist final state and close the database connection."""
        if self._db is not None:
            try:
                await self.persist()
                await self._db.close()
                logger.debug("Memory database closed")
            except Exception:
                logger.exception("Error closing memory database")
            finally:
                self._db = None
