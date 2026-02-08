"""Agent memory — simple recency-based memory with keyword retrieval.

Stores experiences as text strings. Retrieves relevant memories using
keyword matching. Keeps a fixed-size buffer, dropping oldest memories.
"""

from collections import deque


class Memory:
    """Simple in-memory storage for agent experiences."""

    def __init__(self, max_size: int = 500) -> None:
        self._memories: deque[str] = deque(maxlen=max_size)

    def store(self, memory: str) -> None:
        """Store a new memory."""
        if memory and memory not in self._memories:
            self._memories.append(memory)

    def recall(self, context: str, limit: int = 5) -> list[str]:
        """Recall memories relevant to the given context.

        Uses simple keyword overlap scoring.
        """
        if not self._memories:
            return []

        context_words = set(context.lower().split())

        scored: list[tuple[float, str]] = []
        for i, mem in enumerate(self._memories):
            mem_words = set(mem.lower().split())
            overlap = len(context_words & mem_words)
            # Recency bonus: newer memories get a small boost
            recency = i / len(self._memories)
            score = overlap + recency * 0.5
            scored.append((score, mem))

        scored.sort(key=lambda x: -x[0])
        return [mem for _, mem in scored[:limit]]

    def get_recent(self, limit: int = 10) -> list[str]:
        """Get the most recent memories."""
        return list(self._memories)[-limit:]

    def size(self) -> int:
        return len(self._memories)

    def clear(self) -> None:
        self._memories.clear()
