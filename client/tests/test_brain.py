"""Tests for the AgentBrain decision engine — JSON parsing, fallback, token tracking."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from agentburg_shared.protocol.messages import ActionType

from agentburg_client.brain import AgentBrain, TokenUsage
from agentburg_client.config import AgentConfig

# ---------- _extract_json ----------


class TestExtractJson:
    """Test JSON extraction from various LLM output formats."""

    def _extract(self, raw: str) -> dict:
        return AgentBrain._extract_json(raw)

    def test_plain_json(self):
        raw = '{"action": "buy", "params": {"item": "wheat"}, "reasoning": "good price"}'
        result = self._extract(raw)
        assert result["action"] == "buy"
        assert result["params"]["item"] == "wheat"

    def test_markdown_json_code_block(self):
        raw = '```json\n{"action": "sell", "params": {"item": "wood"}, "reasoning": "overstocked"}\n```'
        result = self._extract(raw)
        assert result["action"] == "sell"

    def test_markdown_plain_code_block(self):
        raw = '```\n{"action": "idle", "params": {}, "reasoning": "waiting"}\n```'
        result = self._extract(raw)
        assert result["action"] == "idle"

    def test_text_with_embedded_json(self):
        raw = 'I think the best action is: {"action": "buy", "params": {"item": "iron"}, "reasoning": "need it"}'
        result = self._extract(raw)
        assert result["action"] == "buy"
        assert result["params"]["item"] == "iron"

    def test_text_before_and_after_json(self):
        raw = (
            'Let me think... {"action": "chat", "params": {"message": "hi"}, '
            '"reasoning": "being social"} That is my choice.'
        )
        result = self._extract(raw)
        assert result["action"] == "chat"

    def test_multiple_code_blocks_picks_first_valid(self):
        raw = (
            "Here is my analysis:\n"
            "```\nnot valid json\n```\n"
            "And my decision:\n"
            '```json\n{"action": "deposit", "params": {"amount": 100}, "reasoning": "saving"}\n```'
        )
        result = self._extract(raw)
        assert result["action"] == "deposit"

    def test_nested_braces_in_params(self):
        raw = '{"action": "trade_offer", "params": {"offer_items": {"wheat": 10}}, "reasoning": "trade"}'
        result = self._extract(raw)
        assert result["params"]["offer_items"]["wheat"] == 10

    def test_no_json_raises(self):
        raw = "I cannot decide what to do right now."
        with pytest.raises(json.JSONDecodeError):
            self._extract(raw)

    def test_empty_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            self._extract("")

    def test_code_block_with_extra_whitespace(self):
        raw = '```json\n\n  {"action": "idle", "params": {}, "reasoning": "rest"}  \n\n```'
        result = self._extract(raw)
        assert result["action"] == "idle"


# ---------- _parse_decision ----------


class TestParseDecision:
    """Test decision parsing and validation."""

    def setup_method(self, method):
        from agentburg_client.config import AgentConfig

        self.brain = AgentBrain(AgentConfig())

    def test_valid_action(self):
        raw = '{"action": "buy", "params": {"item": "wheat", "price": 50}, "reasoning": "low price"}'
        result = self.brain._parse_decision(raw)
        assert result["action"] == ActionType.BUY
        assert result["params"]["item"] == "wheat"
        assert result["reasoning"] == "low price"

    def test_invalid_action_falls_back_to_idle(self):
        raw = '{"action": "fly_to_moon", "params": {}, "reasoning": "adventure"}'
        result = self.brain._parse_decision(raw)
        assert result["action"] == ActionType.IDLE
        assert "Invalid action" in result["reasoning"]

    def test_missing_action_defaults_to_idle(self):
        raw = '{"params": {"item": "wood"}, "reasoning": "no action specified"}'
        result = self.brain._parse_decision(raw)
        assert result["action"] == ActionType.IDLE

    def test_non_dict_params_replaced_with_empty(self):
        raw = '{"action": "idle", "params": "invalid", "reasoning": "test"}'
        result = self.brain._parse_decision(raw)
        assert result["action"] == ActionType.IDLE
        assert result["params"] == {}

    def test_non_dict_json_returns_idle(self):
        raw = '["not", "a", "dict"]'
        result = self.brain._parse_decision(raw)
        assert result["action"] == ActionType.IDLE

    def test_unparseable_returns_idle(self):
        raw = "This is not JSON at all and has no braces"
        result = self.brain._parse_decision(raw)
        assert result["action"] == ActionType.IDLE
        assert "Failed to parse" in result["reasoning"]

    def test_all_action_types_accepted(self):
        for action in ActionType:
            raw = json.dumps({"action": action.value, "params": {}, "reasoning": "test"})
            result = self.brain._parse_decision(raw)
            assert result["action"] == action


# ---------- TokenUsage ----------


class TestTokenUsage:
    """Test token usage tracking."""

    def test_initial_state(self):
        usage = TokenUsage()
        assert usage.total_input_tokens == 0
        assert usage.total_output_tokens == 0
        assert usage.total_decisions == 0
        assert usage.total_failures == 0

    def test_record_increments(self):
        usage = TokenUsage()
        usage.record(100, 50, "test-model", 150.0)
        assert usage.total_input_tokens == 100
        assert usage.total_output_tokens == 50
        assert usage.total_decisions == 1

    def test_multiple_records_cumulate(self):
        usage = TokenUsage()
        usage.record(100, 50, "model-a", 100.0)
        usage.record(200, 80, "model-b", 200.0)
        assert usage.total_input_tokens == 300
        assert usage.total_output_tokens == 130
        assert usage.total_decisions == 2

    def test_record_failure(self):
        usage = TokenUsage()
        usage.record_failure()
        usage.record_failure()
        assert usage.total_failures == 2
        assert usage.total_decisions == 0


# ---------- AgentBrain.decide (integration with mocked LLM) ----------


class TestDecide:
    """Test the full decide() flow with a mocked LLM."""

    @pytest.mark.asyncio
    async def test_decide_success(self, default_config: AgentConfig):
        brain = AgentBrain(default_config)

        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(
                message=AsyncMock(
                    content='{"action": "buy", "params": {"item": "wheat", "price": 50}, "reasoning": "good deal"}'
                )
            )
        ]
        mock_response.usage = AsyncMock(prompt_tokens=100, completion_tokens=50)

        with patch("litellm.acompletion", return_value=mock_response):
            tick_data = {
                "tick": 42,
                "agent": {
                    "balance": 10000, "inventory": {}, "location": "market",
                    "reputation": 500, "credit_score": 600,
                },
                "market": {"prices": {"wheat": 50}},
                "observations": [],
            }
            decision = await brain.decide(tick_data)

        assert decision["action"] == ActionType.BUY
        assert decision["params"]["item"] == "wheat"
        assert brain.token_usage.total_decisions == 1

    @pytest.mark.asyncio
    async def test_decide_timeout_falls_back_to_idle(self, default_config: AgentConfig):
        """When LLM times out on all retries, agent should IDLE."""
        default_config.llm.timeout = 0.01
        default_config.llm.max_retries = 1
        brain = AgentBrain(default_config)

        async def slow_llm(*args, **kwargs):
            import asyncio
            await asyncio.sleep(10)  # Will be cancelled by timeout

        with patch("litellm.acompletion", side_effect=slow_llm):
            tick_data = {
                "tick": 1,
                "agent": {"balance": 5000},
                "market": {},
                "observations": [],
            }
            decision = await brain.decide(tick_data)

        assert decision["action"] == ActionType.IDLE
        assert "unavailable" in decision["reasoning"].lower() or "retries" in decision["reasoning"].lower()
        assert brain.token_usage.total_failures >= 1

    @pytest.mark.asyncio
    async def test_decide_llm_error_falls_back_to_idle(self, default_config: AgentConfig):
        """When LLM raises an exception, agent should IDLE."""
        default_config.llm.max_retries = 1
        brain = AgentBrain(default_config)

        with patch("litellm.acompletion", side_effect=RuntimeError("API error")):
            decision = await brain.decide({
                "tick": 1,
                "agent": {"balance": 0},
                "market": {},
                "observations": [],
            })

        assert decision["action"] == ActionType.IDLE
        assert brain.token_usage.total_failures >= 1


# ---------- Brain memory integration ----------


class TestBrainMemory:
    """Test brain-memory interaction."""

    def test_process_observation_stores_memory(self, default_config: AgentConfig):
        brain = AgentBrain(default_config)
        brain.process_observation("Wheat price dropped to 30", tick=5)
        assert brain.memory.size() == 1
        memories = brain.memory.recall("wheat price", limit=1)
        assert len(memories) == 1
        assert "Wheat price dropped" in memories[0]

    def test_process_interaction_stores_memory(self, default_config: AgentConfig):
        brain = AgentBrain(default_config)
        brain.process_interaction("Traded with Agent Bob", tick=10)
        assert brain.memory.size() == 1
