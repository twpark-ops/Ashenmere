"""Agent brain — LLM-powered decision engine.

Takes world observations and produces action decisions using the configured LLM.
"""

import json
import logging
from typing import Any

import litellm

from agentburg_client.config import AgentConfig
from agentburg_client.memory import Memory
from agentburg_shared.protocol.messages import ActionType, QueryType

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

AVAILABLE QUERIES (use these to gather info before acting):
- market_prices: Current prices for all items
- my_balance: Your current financial status
- my_inventory: What you own
- market_orders: Open buy/sell orders
- agent_info: Info about another agent (params: agent_id)
- business_list: Active businesses

You must respond with EXACTLY ONE action in this JSON format:
{{"action": "<action_type>", "params": {{...}}, "reasoning": "brief explanation"}}

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


class AgentBrain:
    """LLM-powered agent decision engine."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.memory = Memory(max_size=config.memory_size)

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

        Returns dict with 'action', 'params', and 'reasoning' keys.
        """
        # Build the decision prompt
        agent = tick_data.get("agent", {})
        market = tick_data.get("market", {})
        observations = tick_data.get("observations", [])

        # Get relevant memories
        context = f"tick={tick_data.get('tick', 0)} balance={agent.get('balance', 0)}"
        memories = self.memory.recall(context, limit=5)

        prompt = DECISION_PROMPT.format(
            tick=tick_data.get("tick", 0),
            balance=agent.get("balance", 0),
            inventory=json.dumps(agent.get("inventory", {})),
            location=agent.get("location", "unknown"),
            reputation=agent.get("reputation", 500),
            credit_score=agent.get("credit_score", 500),
            market_info=self._format_market(market),
            observations="\n".join(f"- {o}" for o in observations) or "- Nothing notable happened.",
            memories="\n".join(f"- {m}" for m in memories) or "- No relevant memories.",
        )

        try:
            # Build model string for LiteLLM
            model = self._get_model_string()

            response = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.config.llm.temperature,
                max_tokens=self.config.llm.max_tokens,
                api_key=self.config.llm.api_key or None,
            )

            raw_text = response.choices[0].message.content.strip()
            decision = self._parse_decision(raw_text)

            # Store the decision as a memory
            self.memory.store(
                f"Tick {tick_data.get('tick', 0)}: Chose {decision['action']} — {decision.get('reasoning', '')}"
            )

            return decision

        except Exception as e:
            logger.exception("LLM decision failed")
            # Fallback to idle
            return {"action": ActionType.IDLE, "params": {}, "reasoning": f"LLM error: {e}"}

    def process_observation(self, observation: str) -> None:
        """Store an observation as a memory."""
        self.memory.store(observation)

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
        """Parse LLM output into a structured decision."""
        # Try to extract JSON from the response
        try:
            # Handle markdown code blocks
            if "```" in raw:
                json_str = raw.split("```")[1]
                if json_str.startswith("json"):
                    json_str = json_str[4:]
                data = json.loads(json_str.strip())
            else:
                data = json.loads(raw)

            action = data.get("action", "idle")
            # Validate action type
            try:
                action = ActionType(action)
            except ValueError:
                action = ActionType.IDLE

            return {
                "action": action,
                "params": data.get("params", {}),
                "reasoning": data.get("reasoning", ""),
            }
        except (json.JSONDecodeError, IndexError, KeyError):
            logger.warning("Failed to parse LLM decision: %s", raw[:200])
            return {"action": ActionType.IDLE, "params": {}, "reasoning": "Failed to parse decision"}

    def _format_market(self, market: dict) -> str:
        """Format market data for the prompt."""
        prices = market.get("prices", {})
        if not prices:
            return "No market data available."

        lines = []
        for item, price in sorted(prices.items()):
            trending = ""
            if item in market.get("trending_up", []):
                trending = " [TRENDING UP]"
            elif item in market.get("trending_down", []):
                trending = " [TRENDING DOWN]"
            lines.append(f"  {item}: {price} coins{trending}")

        return "\n".join(lines)
