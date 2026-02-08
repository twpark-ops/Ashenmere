"""Tests for the agent memory system — store, recall, prune, persistence."""

import tempfile

import pytest

from agentburg_client.memory import Memory, MemoryCategory, MemoryEntry, compute_importance

# ---------- compute_importance ----------


class TestComputeImportance:
    """Test heuristic importance scoring."""

    def test_base_importance(self):
        score = compute_importance("nothing special happened")
        assert 0.4 <= score <= 0.6

    def test_high_keywords_boost(self):
        score = compute_importance("Agent went bankrupt due to failed investment")
        base = compute_importance("nothing happened")
        assert score > base

    def test_decision_category_boost(self):
        obs = compute_importance("bought wheat", MemoryCategory.OBSERVATION)
        dec = compute_importance("bought wheat", MemoryCategory.DECISION)
        assert dec > obs

    def test_knowledge_category_highest_base(self):
        know = compute_importance("simple fact", MemoryCategory.KNOWLEDGE)
        obs = compute_importance("simple fact", MemoryCategory.OBSERVATION)
        assert know > obs

    def test_score_clamped_to_0_1(self):
        # Even with many high keywords, score should not exceed 1.0
        text = " ".join([
            "failed error bankrupt profit loss lawsuit sued critical "
            "warning opportunity deal rich poor reputation credit loan debt"
        ])
        score = compute_importance(text)
        assert 0.0 <= score <= 1.0


# ---------- Memory store / recall ----------


class TestMemoryStore:
    """Test in-memory storage operations."""

    def test_store_and_size(self):
        mem = Memory(max_size=100)
        mem.store("first memory", tick=1)
        mem.store("second memory", tick=2)
        assert mem.size() == 2

    def test_store_deduplication(self):
        mem = Memory(max_size=100)
        mem.store("duplicate", tick=1)
        mem.store("duplicate", tick=2)
        assert mem.size() == 1

    def test_store_empty_string_ignored(self):
        mem = Memory(max_size=100)
        mem.store("", tick=1)
        assert mem.size() == 0

    def test_store_with_explicit_importance(self):
        mem = Memory(max_size=100)
        mem.store("important event", tick=1, importance=0.9)
        entries = mem.get_recent_entries(limit=1)
        assert entries[0].importance == 0.9

    def test_importance_clamped(self):
        mem = Memory(max_size=100)
        mem.store("over limit", tick=1, importance=5.0)
        mem.store("under limit", tick=2, importance=-1.0)
        entries = mem.get_recent_entries(limit=2)
        assert entries[0].importance == 1.0
        assert entries[1].importance == 0.0

    def test_max_size_enforced(self):
        mem = Memory(max_size=3)
        for i in range(10):
            mem.store(f"memory {i}", tick=i)
        assert mem.size() == 3


class TestMemoryRecall:
    """Test keyword-based memory recall."""

    def test_recall_empty_memory(self):
        mem = Memory(max_size=100)
        result = mem.recall("anything", limit=5)
        assert result == []

    def test_recall_returns_relevant_memories(self):
        mem = Memory(max_size=100)
        mem.store("wheat price is 50 coins", tick=1)
        mem.store("iron price is 200 coins", tick=2)
        mem.store("had a nice chat with Bob", tick=3)

        result = mem.recall("wheat price", limit=2)
        assert len(result) <= 2
        # Wheat-related memory should appear
        assert any("wheat" in r.lower() for r in result)

    def test_recall_respects_limit(self):
        mem = Memory(max_size=100)
        for i in range(10):
            mem.store(f"event {i} about wheat", tick=i)
        result = mem.recall("wheat", limit=3)
        assert len(result) == 3

    def test_recall_entries_with_category_filter(self):
        mem = Memory(max_size=100)
        mem.store("observed wheat drop", category=MemoryCategory.OBSERVATION, tick=1)
        mem.store("decided to buy wheat", category=MemoryCategory.DECISION, tick=2)
        mem.store("chatted about wheat", category=MemoryCategory.INTERACTION, tick=3)

        decisions = mem.recall_entries("wheat", limit=5, category=MemoryCategory.DECISION)
        assert len(decisions) == 1
        assert decisions[0].category == MemoryCategory.DECISION


class TestMemoryGetRecent:
    """Test recent memory retrieval."""

    def test_get_recent_returns_strings(self):
        mem = Memory(max_size=100)
        mem.store("first", tick=1)
        mem.store("second", tick=2)
        recent = mem.get_recent(limit=5)
        assert isinstance(recent, list)
        assert all(isinstance(r, str) for r in recent)

    def test_get_recent_entries_returns_full_entries(self):
        mem = Memory(max_size=100)
        mem.store("entry", tick=1, category=MemoryCategory.KNOWLEDGE)
        entries = mem.get_recent_entries(limit=1)
        assert len(entries) == 1
        assert isinstance(entries[0], MemoryEntry)
        assert entries[0].category == MemoryCategory.KNOWLEDGE


# ---------- Memory prune ----------


class TestMemoryPrune:
    """Test memory pruning by importance."""

    def test_prune_no_op_when_small(self):
        mem = Memory(max_size=100)
        mem.store("only one", tick=1)
        pruned = mem.prune(keep=50)
        assert pruned == 0
        assert mem.size() == 1

    def test_prune_keeps_important_memories(self):
        mem = Memory(max_size=100)
        # Low importance
        for i in range(10):
            mem.store(f"boring event {i}", tick=i, importance=0.1)
        # High importance
        mem.store("critical failure detected", tick=20, importance=0.95)

        pruned = mem.prune(keep=5)
        assert pruned > 0
        assert mem.size() == 5

        # The high-importance memory should survive
        remaining = [e.value for e in mem.get_recent_entries(limit=10)]
        assert any("critical failure" in r for r in remaining)

    def test_prune_with_default_keep(self):
        mem = Memory(max_size=10)
        for i in range(10):
            mem.store(f"event {i}", tick=i, importance=0.3)
        pruned = mem.prune()  # default keep = 80% of max_size = 8
        assert mem.size() == 8
        assert pruned == 2


# ---------- Memory clear ----------


class TestMemoryClear:
    def test_clear_removes_all(self):
        mem = Memory(max_size=100)
        for i in range(5):
            mem.store(f"entry {i}", tick=i)
        assert mem.size() == 5
        mem.clear()
        assert mem.size() == 0


# ---------- Memory persistence (async) ----------


class TestMemoryPersistence:
    """Test SQLite persistence via aiosqlite."""

    @pytest.mark.asyncio
    async def test_persist_and_load(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        # Write memories
        mem = Memory(max_size=100, db_path=db_path)
        await mem.initialize()
        mem.store("remembered event 1", tick=1, category=MemoryCategory.OBSERVATION)
        mem.store("remembered event 2", tick=2, category=MemoryCategory.KNOWLEDGE)
        persisted = await mem.persist()
        assert persisted == 2
        await mem.close()

        # Load memories in a new instance
        mem2 = Memory(max_size=100, db_path=db_path)
        await mem2.initialize()
        loaded = await mem2.load()
        assert loaded == 2
        assert mem2.size() == 2

        # Verify content
        recent = mem2.get_recent(limit=10)
        assert "remembered event 1" in recent
        assert "remembered event 2" in recent
        await mem2.close()

    @pytest.mark.asyncio
    async def test_no_db_path_skips_persistence(self):
        mem = Memory(max_size=100, db_path="")
        await mem.initialize()  # No-op
        persisted = await mem.persist()
        assert persisted == 0

    @pytest.mark.asyncio
    async def test_prune_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        mem = Memory(max_size=100, db_path=db_path)
        await mem.initialize()
        for i in range(20):
            mem.store(f"event {i}", tick=i, importance=0.1 + i * 0.04)
        await mem.persist()

        pruned = await mem.prune_db(keep=10)
        assert pruned == 10
        await mem.close()
