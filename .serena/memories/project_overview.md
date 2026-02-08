# AgentBurg Project Overview

## Purpose
AgentBurg is an autonomous AI agent economy simulation platform where AI agents participate in a virtual economy — trading goods, banking, starting businesses, filing lawsuits, and socializing.

## Architecture
- **Server** (Python/FastAPI): World simulation, economy engine, WebSocket API
- **Client** (Python): Agent brain with LLM-powered decision making
- **Dashboard** (React/TypeScript): Real-time visualization
- **Shared** (Python): Protocol definitions shared between server and client

## Tech Stack
- Python 3.13+ / FastAPI / SQLAlchemy 2.0+ / Pydantic 2.10+
- Auth: PyJWT + argon2-cffi
- LLM: LiteLLM + instructor (client-side)
- DB: PostgreSQL 17 + Redis 8 (SQLite for tests)
- Frontend: React 19 + TypeScript / Vite 6
- Tools: uv (pkg mgr), ruff (lint), pytest + pytest-asyncio

## Monorepo Structure
```
server/src/agentburg_server/  — FastAPI server
  api/                         — routes.py, ws.py, deps.py
  engine/                      — tick.py (world simulation loop)
  handlers/                    — action_handler.py, query_handler.py
  models/                      — agent, economy, social, event, user, base
  services/                    — auth, bank, market, court, business, social
  config.py, db.py, main.py
server/tests/                  — 218 tests across 14 files
client/src/agentburg_client/   — Agent brain, memory, config, connection
client/tests/                  — 93 tests
shared/agentburg_shared/       — Protocol messages
dashboard/src/                 — React dashboard
```
