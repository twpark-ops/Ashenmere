# Code Style and Conventions

## Language
- Code, comments, docstrings: English only
- Conversation with user: Korean

## Python Style
- Python 3.13+ features (enum.StrEnum, etc.)
- Type hints on all function signatures
- ruff for linting (E, F, W, I, B, S, UP, SIM rules)
- Line length: 120 chars
- Import sorting: isort (via ruff I001)
- Docstrings: triple-quote, first line is summary

## Testing
- pytest + pytest-asyncio (anyio backend)
- SQLite in-memory for test DB (conftest.py handles PG→SQLite type compilation)
- `db_session` fixture with rollback isolation
- `_override_db` pattern for handler tests (overrides `_db.get_session_factory`)
- Marker: `@pytest.mark.anyio` for async tests

## Patterns
- `from __future__ import annotations` in all files
- `import X as _x` for testable module-level singletons (db.py pattern)
- FastAPI Depends() in route defaults (B008 suppressed)
- Pydantic BaseModel for all protocol messages
- StrEnum for all enums (not str+Enum)

## DB
- SQLAlchemy async with `async_sessionmaker`
- Explicit queries instead of lazy relationship loading (avoids MissingGreenlet)
- SAEnum stores uppercase enum names (e.g., 'LOAN' not 'loan')
