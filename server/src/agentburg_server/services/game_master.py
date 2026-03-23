"""AI Game Master — autonomous world operator powered by LLM.

The Game Master observes the world state every N ticks and makes decisions
to keep the simulation interesting and balanced. It can trigger events,
adjust the economy, make announcements, and manage seasons.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import litellm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent
from agentburg_server.models.economy import Trade
from agentburg_server.models.event import EventCategory, WorldEventLog
from agentburg_server.models.season import Season, SeasonStatus

logger = logging.getLogger(__name__)

GM_SYSTEM_PROMPT = """You are the Game Master of Ashenmere, an autonomous AI that manages a living world.

You observe the economy every simulated day and make ONE decision to keep things interesting.

WORLD STATE will be provided with: agent count, total trades, wealth distribution,
active events, current season info, and the day/tick number.

YOUR AVAILABLE ACTIONS (pick exactly ONE as JSON):

1. {"action": "trigger_event", "params": {"event_name": "storm"}}
   Events: storm, drought, harvest, fog, caravan, panic, gold_rush, embargo,
           festival, plague, crime_wave, tournament, earthquake, dragon

2. {"action": "announce", "params": {"message": "Your announcement text"}}
   Broadcast a message to all agents (rumors, warnings, celebrations).

3. {"action": "adjust_production", "params": {"item": "wheat", "multiplier": 1.5, "duration": 3}}
   Temporarily boost or reduce production of an item.

4. {"action": "observe", "params": {}}
   Do nothing this cycle. The world is fine as is.

GUIDELINES:
- If trade volume is LOW (< 5 trades/day), trigger events to create urgency
- If one agent has 3x more wealth than average, create events that challenge the rich
- If the world is quiet for 3+ days, NEVER observe — always act
- Keep announcements in-character (gritty Ashenmere tone)
- Use "observe" sparingly — only when economy is genuinely healthy
- Be dramatic. This is entertainment.

Respond with exactly ONE action in JSON format."""

GM_DECISION_PROMPT = """WORLD STATE (Day {day}, Tick {tick}):

Active agents: {agent_count}
Total trades this season: {total_trades}
Trades last day: {recent_trades}

Wealth distribution:
{wealth_summary}

Active world events: {active_events}

Current season: {season_name} ({season_status})
Days since last GM action: {days_since_action}

What is your decision? Respond with ONE action in JSON."""


@dataclass
class GMAction:
    """A Game Master decision."""
    action: str
    params: dict[str, Any]
    reasoning: str = ""


class GameMaster:
    """AI-powered world operator. Evaluates and acts every N ticks."""

    def __init__(self) -> None:
        self.model = os.getenv("GM_MODEL", "gpt-4o-mini")
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.last_action_tick = 0
        self.ticks_per_evaluation = 6  # every simulated day

    async def should_evaluate(self, tick: int) -> bool:
        """Check if it's time for a GM evaluation."""
        return tick > 0 and tick % self.ticks_per_evaluation == 0

    async def evaluate_and_act(self, session: AsyncSession, tick: int, day: int) -> GMAction | None:
        """Observe world state, call LLM, execute decision."""
        if not self.api_key:
            logger.debug("Game Master disabled — no OPENAI_API_KEY")
            return None

        try:
            # Gather world state
            world_state = await self._gather_state(session, tick, day)

            # Call LLM
            action = await self._decide(world_state)

            if action and action.action != "observe":
                # Execute the action
                await self._execute(session, tick, action)
                self.last_action_tick = tick
                logger.info("GM Action [Day %d]: %s — %s", day, action.action, action.reasoning[:80])
            else:
                logger.info("GM Observation [Day %d]: world is stable", day)

            return action

        except Exception:
            logger.exception("Game Master evaluation failed at tick %d", tick)
            return None

    async def _gather_state(self, session: AsyncSession, tick: int, day: int) -> dict:
        """Collect world state for LLM context."""
        # Agent count and wealth
        agents_result = await session.execute(select(Agent.name, Agent.balance).order_by(Agent.balance.desc()))
        agents = agents_result.all()
        agent_count = len(agents)

        wealth_lines = []
        for name, balance in agents[:8]:
            wealth_lines.append(f"  {name}: {balance} coins")

        # Recent trades
        total_trades = await session.scalar(select(func.count()).select_from(Trade)) or 0
        recent_ticks = max(0, tick - self.ticks_per_evaluation)
        recent_trades = await session.scalar(
            select(func.count()).select_from(Trade).where(Trade.tick >= recent_ticks)
        ) or 0

        # Current season
        season = await session.scalar(
            select(Season).where(Season.status == SeasonStatus.ACTIVE).limit(1)
        )
        season_name = season.name if season else "No active season"
        season_status = season.status.value if season else "none"

        days_since = (tick - self.last_action_tick) // self.ticks_per_evaluation

        return {
            "day": day,
            "tick": tick,
            "agent_count": agent_count,
            "total_trades": total_trades,
            "recent_trades": recent_trades,
            "wealth_summary": "\n".join(wealth_lines) or "  No agents",
            "active_events": "None",
            "season_name": season_name,
            "season_status": season_status,
            "days_since_action": days_since,
        }

    async def _decide(self, state: dict) -> GMAction | None:
        """Call LLM to get GM decision."""
        prompt = GM_DECISION_PROMPT.format(**state)

        response = await litellm.acompletion(
            model=self.model,
            messages=[
                {"role": "system", "content": GM_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
            max_tokens=300,
            api_key=self.api_key or None,
        )

        text = response.choices[0].message.content.strip()

        # Parse JSON from response
        try:
            # Handle markdown code blocks
            if "```" in text:
                text = text.split("```")[1].strip()
                if text.startswith("json"):
                    text = text[4:].strip()

            data = json.loads(text)
            return GMAction(
                action=data.get("action", "observe"),
                params=data.get("params", {}),
                reasoning=data.get("reasoning", ""),
            )
        except (json.JSONDecodeError, KeyError):
            logger.warning("GM returned unparseable response: %s", text[:100])
            return GMAction(action="observe", params={}, reasoning="Failed to parse")

    async def _execute(self, session: AsyncSession, tick: int, action: GMAction) -> None:
        """Execute a GM action."""
        if action.action == "announce":
            message = action.params.get("message", "The Game Master speaks...")
            event = WorldEventLog(
                tick=tick,
                category=EventCategory.WORLD,
                event_type="gm_announcement",
                description=f"[GAME MASTER] {message}",
                data={"source": "game_master", "action": action.action},
            )
            session.add(event)

        elif action.action == "trigger_event":
            event_name = action.params.get("event_name", "unknown")
            event = WorldEventLog(
                tick=tick,
                category=EventCategory.WORLD,
                event_type="gm_event",
                description=f"[GAME MASTER triggers: {event_name}] {action.reasoning}",
                data={"source": "game_master", "event_name": event_name},
            )
            session.add(event)

        elif action.action == "adjust_production":
            item = action.params.get("item", "unknown")
            multiplier = action.params.get("multiplier", 1.0)
            event = WorldEventLog(
                tick=tick,
                category=EventCategory.WORLD,
                event_type="gm_adjustment",
                description=f"[GAME MASTER adjusts {item} production x{multiplier}]",
                data={"source": "game_master", "item": item, "multiplier": multiplier},
            )
            session.add(event)

        await session.flush()


# Singleton
game_master = GameMaster()
