"""Agent brain — LLM-powered decision engine.

Takes world observations and produces action decisions using the configured LLM.
Includes timeout handling, retry with exponential backoff, structured output
validation, and token usage tracking.
"""

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import litellm
from agentburg_shared.protocol.messages import ActionType

from agentburg_client.config import AgentConfig
from agentburg_client.memory import Memory, MemoryCategory

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """You are {name}, a {title} in the world of AgentBurg.

{bio}

YOUR PERSONALITY TRAITS:
- Risk tolerance: {risk_tolerance}/1.0 (higher = more risk-taking)
- Aggression: {aggression}/1.0 (higher = more confrontational)
- Sociability: {sociability}/1.0 (higher = more social)
- Greed: {greed}/1.0 (higher = more profit-focused)
- Honesty: {honesty}/1.0 (higher = more truthful)

YOUR GOALS:
{goals}

AVAILABLE ACTIONS:
- buy: Purchase items (params: item, price, quantity)
- sell: Sell items (params: item, price, quantity)
- deposit: Deposit money to bank (params: account_id, amount)
- withdraw: Withdraw money from bank (params: account_id, amount)
- borrow: Take a loan (params: amount)
- repay: Repay a loan (params: account_id, amount)
- invest: Invest in a business (params: business_id, amount)
- sue: File a lawsuit (params: target_id, case_type, description, evidence)
- chat: Talk to another agent (params: target_id, message)
- start_business: Start a business (params: name, type, location)
- set_price: Set prices for your business (params: business_id, item, price)
- idle: Do nothing this turn

IMPORTANT: You can ONLY choose from the actions listed above. Do NOT use query names like
"market_prices" or "my_balance" as actions — that information is already provided to you
in the CURRENT SITUATION below.

You must respond with EXACTLY ONE action in this JSON format:
{{"action": "<action_type>", "params": {{...}}, "reasoning": "brief explanation"}}

MARKET PRICE GUIDE (approximate fair prices per unit):
wheat: 50, bread: 80, wood: 40, stone: 60, iron: 120, gold: 500,
fish: 45, wool: 55, cloth: 100, tools: 150, leather: 90, meat: 70,
ale: 35, medicine: 200, spices: 180

TRADING RULES (FOLLOW STRICTLY):
1. If you have items in inventory with quantity > 10, SELL some. Pick your MOST abundant item.
2. If someone has a BUY ORDER for an item you own, sell to match it.
3. If someone has items FOR SALE, buy them if price is good.
4. Place buy orders for items you DON'T have — try different items each turn!
5. NEVER idle. NEVER buy what you already have 20+ of.
6. DIVERSIFY: Don't trade the same item twice in a row. Rotate through different items.
7. HIGH-VALUE items (gold, tools, spices, medicine) are more profitable per trade.
8. Prices are PER UNIT in coins. Sell at or above fair price, buy at or below.

Be strategic. Think about long-term consequences. Stay in character."""

DECISION_PROMPT = """CURRENT SITUATION (Tick {tick}):
Balance: {balance} coins
Inventory: {inventory}
Location: {location}
Reputation: {reputation}/1000
Credit Score: {credit_score}/1000

MARKET:
{market_info}

RECENT OBSERVATIONS:
{observations}

RELEVANT MEMORIES:
{memories}

What will you do? Respond with a single action in JSON format."""

# Set of valid action type values for fast lookup
_VALID_ACTIONS: frozenset[str] = frozenset(a.value for a in ActionType)


@dataclass
class TokenUsage:
    """Tracks cumulative LLM token usage across decisions."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_decisions: int = 0
    total_failures: int = 0
    _history: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        duration_ms: float,
    ) -> None:
        """Record token usage for a single LLM call."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_decisions += 1
        entry = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": model,
            "duration_ms": round(duration_ms, 1),
        }
        self._history.append(entry)
        logger.info(
            "LLM tokens — in: %d, out: %d, model: %s, latency: %.0fms | cumulative — in: %d, out: %d, decisions: %d",
            input_tokens,
            output_tokens,
            model,
            duration_ms,
            self.total_input_tokens,
            self.total_output_tokens,
            self.total_decisions,
        )

    def record_failure(self) -> None:
        """Record a failed LLM call."""
        self.total_failures += 1


class AgentBrain:
    """LLM-powered agent decision engine with retry, timeout, and token tracking."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.memory = Memory(
            max_size=config.memory_size,
            db_path=config.memory_db_path,
        )
        self.token_usage = TokenUsage()

        # Configure LiteLLM
        if config.llm.api_base:
            litellm.api_base = config.llm.api_base

        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            name=config.personality.name,
            title=config.personality.title,
            bio=config.personality.bio,
            risk_tolerance=config.personality.risk_tolerance,
            aggression=config.personality.aggression,
            sociability=config.personality.sociability,
            greed=config.personality.greed,
            honesty=config.personality.honesty,
            goals="\n".join(f"- {g}" for g in config.personality.goals) or "- Survive and prosper",
        )

    async def decide(self, tick_data: dict[str, Any]) -> dict[str, Any]:
        """Make a decision based on current world state.

        Calls the LLM with timeout and retry logic.
        Returns dict with 'action', 'params', and 'reasoning' keys.
        Falls back to IDLE if all retries fail.
        """
        # Build the decision prompt
        agent = tick_data.get("agent", {})
        market = tick_data.get("market", {})
        observations = tick_data.get("observations", [])
        tick = tick_data.get("tick", 0)

        # Build context for memory recall with key agent state
        context_parts = [
            f"tick={tick}",
            f"balance={agent.get('balance', 0)}",
            f"location={agent.get('location', 'unknown')}",
            f"reputation={agent.get('reputation', 500)}",
        ]
        # Include inventory items for market-related memory recall
        inventory = agent.get("inventory", {})
        if inventory:
            context_parts.append(f"inventory={' '.join(inventory.keys())}")
        context = " ".join(context_parts)
        memories = self.memory.recall(context, limit=5)

        prompt = DECISION_PROMPT.format(
            tick=tick,
            balance=agent.get("balance", 0),
            inventory=json.dumps(agent.get("inventory", {})),
            location=agent.get("location", "unknown"),
            reputation=agent.get("reputation", 500),
            credit_score=agent.get("credit_score", 500),
            market_info=self._format_market(market),
            observations="\n".join(f"- {o}" for o in observations) or "- Nothing notable happened.",
            memories="\n".join(f"- {m}" for m in memories) or "- No relevant memories.",
        )

        max_retries = self.config.llm.max_retries
        timeout = self.config.llm.timeout
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                decision = await self._call_llm_with_timeout(prompt, timeout)

                # Store the decision as a memory
                self.memory.store(
                    f"Tick {tick}: Chose {decision['action']} — {decision.get('reasoning', '')}",
                    category=MemoryCategory.DECISION,
                    tick=tick,
                )
                return decision

            except TimeoutError:
                last_error = TimeoutError(f"LLM call timed out after {timeout}s")
                logger.warning(
                    "LLM call timed out (attempt %d/%d, timeout=%.1fs)",
                    attempt,
                    max_retries,
                    timeout,
                )
                self.token_usage.record_failure()

            except Exception as e:
                last_error = e
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s",
                    attempt,
                    max_retries,
                    e,
                )
                self.token_usage.record_failure()

            # Exponential backoff with jitter between retries
            if attempt < max_retries:
                backoff = min(2 ** (attempt - 1), 10.0) + random.uniform(0, 1)  # noqa: S311
                logger.debug("Retrying in %.1fs...", backoff)
                await asyncio.sleep(backoff)

        # All retries exhausted — fall back to idle
        logger.error(
            "LLM failed after %d attempts. Last error: %s. Falling back to IDLE.",
            max_retries,
            last_error,
        )
        fallback = {
            "action": ActionType.IDLE,
            "params": {},
            "reasoning": f"LLM unavailable after {max_retries} retries: {last_error}",
        }
        self.memory.store(
            f"Tick {tick}: LLM failure, forced IDLE — {last_error}",
            category=MemoryCategory.DECISION,
            tick=tick,
            importance=0.8,
        )
        return fallback

    async def _call_llm_with_timeout(self, prompt: str, timeout: float) -> dict[str, Any]:
        """Call the LLM with a timeout wrapper and parse the response.

        Raises asyncio.TimeoutError if the call exceeds the timeout.
        Raises ValueError if the response cannot be parsed.
        """
        model = self._get_model_string()
        start_time = time.monotonic()

        response = await asyncio.wait_for(
            litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.config.llm.temperature,
                max_tokens=self.config.llm.max_tokens,
                api_key=self.config.llm.api_key or None,
            ),
            timeout=timeout,
        )

        elapsed_ms = (time.monotonic() - start_time) * 1000

        # Extract token usage from response
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        self.token_usage.record(input_tokens, output_tokens, model, elapsed_ms)

        raw_text = response.choices[0].message.content.strip()
        decision = self._parse_decision(raw_text)
        return decision

    def process_observation(self, observation: str, tick: int = 0) -> None:
        """Store an observation as a memory."""
        self.memory.store(
            observation,
            category=MemoryCategory.OBSERVATION,
            tick=tick,
        )

    def process_interaction(self, interaction: str, tick: int = 0) -> None:
        """Store an agent interaction as a memory."""
        self.memory.store(
            interaction,
            category=MemoryCategory.INTERACTION,
            tick=tick,
        )

    def _get_model_string(self) -> str:
        """Build LiteLLM model string from config."""
        provider = self.config.llm.provider
        model = self.config.llm.model

        if provider == "ollama":
            return f"ollama/{model}"
        elif provider == "anthropic":
            return model  # LiteLLM handles anthropic/ prefix
        elif provider == "openai":
            return model
        else:
            return f"{provider}/{model}"

    def _parse_decision(self, raw: str) -> dict[str, Any]:
        """Parse LLM output into a structured decision.

        Handles multiple JSON formats that LLMs commonly produce:
        1. Plain JSON: {"action": "buy", ...}
        2. Markdown code block: ```json\n{...}\n```
        3. Text with embedded JSON: "I think... {"action": "buy", ...}"
        4. Multiple code blocks: extracts the first valid one

        Validates the action type against the known set. If the output is
        unparseable or contains an invalid action, logs a warning and
        returns IDLE.
        """
        try:
            data = self._extract_json(raw)

            if not isinstance(data, dict):
                logger.warning("LLM returned non-dict JSON: %s", type(data).__name__)
                return self._idle_decision("LLM returned non-dict response")

            action_str = data.get("action", "idle")

            # Validate action type against known enum values
            if action_str not in _VALID_ACTIONS:
                logger.warning(
                    "LLM returned invalid action '%s', falling back to idle. Valid actions: %s",
                    action_str,
                    sorted(_VALID_ACTIONS),
                )
                return self._idle_decision(f"Invalid action '{action_str}'")

            action = ActionType(action_str)

            params = data.get("params", {})
            if not isinstance(params, dict):
                logger.warning("LLM returned non-dict params: %s", type(params).__name__)
                params = {}

            return {
                "action": action,
                "params": params,
                "reasoning": str(data.get("reasoning", "")),
            }
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.warning("Failed to parse LLM decision (%s): %s", type(e).__name__, raw[:200])
            return self._idle_decision("Failed to parse LLM output")

    @staticmethod
    def _extract_json(raw: str) -> Any:
        """Extract JSON from LLM output, handling code blocks and embedded JSON.

        Tries multiple strategies in order:
        1. Markdown fenced code block (```json ... ``` or ``` ... ```)
        2. Direct JSON parse of the entire string
        3. Find the first { ... } substring and parse it

        Raises json.JSONDecodeError if no valid JSON is found.
        """
        # Strategy 1: Extract from markdown code blocks
        code_block_re = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
        for match in code_block_re.finditer(raw):
            content = match.group(1).strip()
            if content:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    continue

        # Strategy 2: Direct parse
        stripped = raw.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Strategy 3: Find the outermost { ... } brace pair
        start = stripped.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(stripped)):
                if stripped[i] == "{":
                    depth += 1
                elif stripped[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = stripped[start : i + 1]
                        return json.loads(candidate)

        raise json.JSONDecodeError("No valid JSON found in LLM output", raw, 0)

    @staticmethod
    def _idle_decision(reason: str) -> dict[str, Any]:
        """Create a fallback IDLE decision."""
        return {"action": ActionType.IDLE, "params": {}, "reasoning": reason}

    def _format_market(self, market: dict) -> str:
        """Format market data for the prompt."""
        lines = []

        prices = market.get("prices", {})
        if prices:
            lines.append("RECENT PRICES:")
            for item, price in sorted(prices.items()):
                lines.append(f"  {item}: {price} coins")

        open_orders = market.get("open_orders", [])
        if open_orders:
            sells = [o for o in open_orders if o["side"] == "sell"]
            buys = [o for o in open_orders if o["side"] == "buy"]
            if sells:
                lines.append("ITEMS FOR SALE (you can buy these):")
                for o in sells[:10]:
                    lines.append(f"  {o['item']}: {o['quantity']}x @ {o['price']} coins each")
            if buys:
                lines.append("BUY ORDERS (you can sell to these):")
                for o in buys[:10]:
                    lines.append(f"  {o['item']}: {o['quantity']}x wanted @ {o['price']} coins each")

        if not lines:
            return "Market is empty. Place the first buy or sell order!"

        return "\n".join(lines)
