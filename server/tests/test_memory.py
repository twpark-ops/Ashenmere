"""Tests for the agent memory system — storage, recall, categories, pruning."""

from agentburg_client.memory import Memory, MemoryCategory, compute_importance


# ---------------------------------------------------------------------------
# Basic storage and recall
# ---------------------------------------------------------------------------


def test_store_and_recall():
    """Basic store and recall should work."""
    mem = Memory(max_size=10)
    mem.store("Bought wheat at 100 coins")
    mem.store("Sold bread at 200 coins")
    mem.store("Filed lawsuit against trader Bob")

    assert mem.size() == 3


def test_recall_relevant():
    """Recall should return most relevant memories by keyword overlap."""
    mem = Memory(max_size=10)
    mem.store("Bought wheat at 100 coins")
    mem.store("Sold bread at 200 coins")
    mem.store("Filed lawsuit against trader Bob")
    mem.store("Bank interest credited 50 coins")

    results = mem.recall("wheat bought coins", limit=2)
    assert len(results) == 2
    # First result should be most relevant (wheat/bought/coins overlap)
    assert "wheat" in results[0].lower() or "coins" in results[0].lower()


def test_max_size():
    """Deque should evict oldest entries when max_size is exceeded."""
    mem = Memory(max_size=3)
    mem.store("memory 1")
    mem.store("memory 2")
    mem.store("memory 3")
    mem.store("memory 4")  # Should evict memory 1

    assert mem.size() == 3
    recent = mem.get_recent(10)
    assert "memory 1" not in recent
    assert "memory 4" in recent


def test_no_duplicates():
    """Duplicate memories should be deduplicated."""
    mem = Memory(max_size=10)
    mem.store("same memory")
    mem.store("same memory")

    assert mem.size() == 1


def test_empty_recall():
    """Recall on empty memory should return empty list."""
    mem = Memory(max_size=10)
    results = mem.recall("anything")
    assert results == []


def test_clear():
    """Clear should remove all memories."""
    mem = Memory(max_size=10)
    mem.store("something")
    mem.clear()
    assert mem.size() == 0


# ---------------------------------------------------------------------------
# Category support
# ---------------------------------------------------------------------------


def test_store_with_category():
    """Storing with category should preserve the category."""
    mem = Memory(max_size=10)
    mem.store("Market crashed", category=MemoryCategory.KNOWLEDGE, tick=5)

    entries = mem.get_recent_entries(1)
    assert len(entries) == 1
    assert entries[0].category == MemoryCategory.KNOWLEDGE
    assert entries[0].tick == 5


def test_recall_entries_with_category_filter():
    """Recall entries should filter by category when specified."""
    mem = Memory(max_size=20)
    mem.store("Bought wheat", category=MemoryCategory.DECISION, tick=1)
    mem.store("Talked to Bob", category=MemoryCategory.INTERACTION, tick=2)
    mem.store("Market up 5%", category=MemoryCategory.KNOWLEDGE, tick=3)
    mem.store("Sold bread", category=MemoryCategory.DECISION, tick=4)

    decisions = mem.recall_entries("trade", category=MemoryCategory.DECISION)
    assert all(e.category == MemoryCategory.DECISION for e in decisions)


# ---------------------------------------------------------------------------
# Importance scoring
# ---------------------------------------------------------------------------


def test_compute_importance_high():
    """High-importance keywords should boost the score."""
    score = compute_importance("Agent went bankrupt and failed")
    assert score > 0.6  # Base 0.5 + keyword bonuses


def test_compute_importance_low():
    """Text without signal words should have base importance."""
    score = compute_importance("Nothing happened today")
    assert score <= 0.55  # Close to base 0.5


def test_compute_importance_knowledge():
    """Knowledge category should have higher baseline."""
    base = compute_importance("something", MemoryCategory.OBSERVATION)
    knowledge = compute_importance("something", MemoryCategory.KNOWLEDGE)
    assert knowledge > base


def test_importance_clamped():
    """Importance should be clamped to [0.0, 1.0]."""
    # Many high-importance keywords
    score = compute_importance(
        "failed error bankrupt loss profit lawsuit warning critical opportunity deal"
    )
    assert score <= 1.0
    assert score >= 0.0


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


def test_prune_keeps_important():
    """Pruning should keep high-importance memories and discard low ones."""
    mem = Memory(max_size=20)

    # Add low-importance memories
    for i in range(15):
        mem.store(f"boring event {i}", tick=i, importance=0.1)

    # Add high-importance memories
    for i in range(5):
        mem.store(f"critical failure {i}", tick=100 + i, importance=0.9)

    assert mem.size() == 20

    pruned = mem.prune(keep=10)

    assert pruned == 10
    assert mem.size() == 10

    # Verify that high-importance memories survived
    recent = mem.get_recent(10)
    critical_count = sum(1 for m in recent if "critical" in m)
    assert critical_count == 5


def test_prune_no_op_when_below_threshold():
    """Prune should be a no-op when memory count is below keep threshold."""
    mem = Memory(max_size=20)
    mem.store("only one memory")

    pruned = mem.prune(keep=10)
    assert pruned == 0
    assert mem.size() == 1


# ---------------------------------------------------------------------------
# Empty string handling
# ---------------------------------------------------------------------------


def test_store_empty_string_ignored():
    """Storing an empty string should be a no-op."""
    mem = Memory(max_size=10)
    mem.store("")
    assert mem.size() == 0
