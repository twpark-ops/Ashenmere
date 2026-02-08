# Suggested Commands

## Testing
```bash
# Server tests
cd server && uv run pytest --tb=short -q
cd server && uv run pytest --cov=agentburg_server --cov-report=term-missing

# Client tests
cd client && uv run pytest --tb=short -q

# Run specific test file
cd server && uv run pytest tests/test_market.py -v
```

## Linting
```bash
# Server
cd server && uv run ruff check src/ tests/

# Client
cd client && uv run ruff check src/ tests/

# Shared
cd shared && uv run ruff check .

# Auto-fix
uv run ruff check --fix src/ tests/
```

## Running
```bash
# Server
cd server && uv run uvicorn agentburg_server.main:app --reload

# Dashboard
cd dashboard && npm run dev

# Full stack
docker compose up
```

## Package Management
```bash
uv add <package>          # Add dependency
uv sync                   # Sync dependencies
uv lock                   # Update lock file
```
