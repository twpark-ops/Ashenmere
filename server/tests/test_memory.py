"""Tests for the agent memory system."""

from agentburg_client.memory import Memory


def test_store_and_recall():
    mem = Memory(max_size=10)
    mem.store("Bought wheat at 100 coins")
    mem.store("Sold bread at 200 coins")
    mem.store("Filed lawsuit against trader Bob")

    assert mem.size() == 3


def test_recall_relevant():
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
    mem = Memory(max_size=10)
    mem.store("same memory")
    mem.store("same memory")

    assert mem.size() == 1


def test_empty_recall():
    mem = Memory(max_size=10)
    results = mem.recall("anything")
    assert results == []


def test_clear():
    mem = Memory(max_size=10)
    mem.store("something")
    mem.clear()
    assert mem.size() == 0
