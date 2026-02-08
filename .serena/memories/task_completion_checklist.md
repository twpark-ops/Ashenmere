# Task Completion Checklist

After completing any code change:

1. **Run tests**: `cd server && uv run pytest --tb=short -q`
2. **Run lint**: `cd server && uv run ruff check src/ tests/`
3. **Check coverage** (if tests added): `uv run pytest --cov=agentburg_server --cov-report=term-missing`
4. **Verify 0 lint errors** across server, client, shared
5. **All tests must pass** before committing
