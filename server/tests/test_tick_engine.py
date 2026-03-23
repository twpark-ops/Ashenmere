"""Tests for the tick engine — world simulation loop logic."""

from __future__ import annotations

from datetime import UTC
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.engine.tick import TickEngine, _process_contract_payments
from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.social import Contract, ContractStatus, ContractType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_agent(
    session: AsyncSession,
    *,
    name: str = "TickAgent",
    balance: int = 10_000,
) -> Agent:
    """Insert and return a fresh Agent for tick tests."""
    agent = Agent(
        id=uuid4(),
        name=name,
        api_token_hash=sha256(f"token-{name}-{uuid4()}".encode()).hexdigest(),
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=balance,
        inventory={},
        location="downtown",
        reputation=500,
        credit_score=700,
    )
    session.add(agent)
    await session.flush()
    return agent


# ---------------------------------------------------------------------------
# TickEngine unit tests
# ---------------------------------------------------------------------------


def test_tick_engine_initial_state():
    """TickEngine should start at tick 0 and not running."""
    engine = TickEngine()
    assert engine.tick == 0
    assert engine.running is False


def test_world_time_calculation():
    """World time should advance correctly with tick count."""
    engine = TickEngine()

    # At tick 0 → midnight of day 0
    t0 = engine.world_time
    assert t0.hour == 0
    assert t0.minute == 0
    assert t0.tzinfo == UTC

    # At tick = ticks_per_day → midnight of day 1
    engine.tick = engine.ticks_per_day
    t1 = engine.world_time
    assert t1.day == 2  # Jan 2 (started Jan 1)

    # At tick = ticks_per_day // 2 → noon
    engine.tick = engine.ticks_per_day // 2
    t2 = engine.world_time
    assert t2.hour == 12


def test_world_time_multi_day():
    """World time should properly handle multiple days."""
    engine = TickEngine()
    engine.tick = engine.ticks_per_day * 7  # 7 days in
    t = engine.world_time
    assert t.day == 8  # Jan 8 (Jan 1 + 7 days)


# ---------------------------------------------------------------------------
# Contract payment processing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_process_contract_payments(db_session: AsyncSession):
    """Active employment contracts should transfer salary on schedule."""
    employer = await _make_agent(db_session, name="Employer", balance=50_000)
    employee = await _make_agent(db_session, name="Employee", balance=1_000)

    contract = Contract(
        id=uuid4(),
        contract_type=ContractType.EMPLOYMENT,
        party_a_id=employer.id,  # employer
        party_b_id=employee.id,  # employee
        status=ContractStatus.ACTIVE,
        tick_start=0,
        terms={"role": "merchant"},
        payment_amount=5_000,
        payment_interval_ticks=10,
    )
    db_session.add(contract)
    await db_session.flush()

    # At tick 10 (first payment due)
    payments = await _process_contract_payments(db_session, tick=10)
    await db_session.flush()

    assert payments == 1
    assert employer.balance == 45_000  # 50_000 - 5_000
    assert employee.balance == 6_000  # 1_000 + 5_000


@pytest.mark.anyio
async def test_process_contract_payments_not_due(db_session: AsyncSession):
    """No payment should be made if the interval hasn't elapsed."""
    employer = await _make_agent(db_session, name="E1", balance=50_000)
    employee = await _make_agent(db_session, name="W1", balance=1_000)

    contract = Contract(
        id=uuid4(),
        contract_type=ContractType.EMPLOYMENT,
        party_a_id=employer.id,
        party_b_id=employee.id,
        status=ContractStatus.ACTIVE,
        tick_start=0,
        terms={},
        payment_amount=5_000,
        payment_interval_ticks=10,
    )
    db_session.add(contract)
    await db_session.flush()

    # At tick 5 (not a multiple of interval=10 from start=0)
    payments = await _process_contract_payments(db_session, tick=5)
    assert payments == 0
    assert employer.balance == 50_000  # unchanged
    assert employee.balance == 1_000  # unchanged


@pytest.mark.anyio
async def test_process_contract_breach_insufficient_funds(db_session: AsyncSession):
    """Employer without enough balance should breach the contract."""
    employer = await _make_agent(db_session, name="BrokeEmployer", balance=1_000)
    employee = await _make_agent(db_session, name="W2", balance=1_000)

    contract = Contract(
        id=uuid4(),
        contract_type=ContractType.EMPLOYMENT,
        party_a_id=employer.id,
        party_b_id=employee.id,
        status=ContractStatus.ACTIVE,
        tick_start=0,
        terms={},
        payment_amount=5_000,
        payment_interval_ticks=10,
    )
    db_session.add(contract)
    await db_session.flush()

    original_rep = employer.reputation
    payments = await _process_contract_payments(db_session, tick=10)
    await db_session.flush()

    assert payments == 0
    assert contract.status == ContractStatus.BREACHED
    assert employer.reputation == max(0, original_rep - 20)
    assert employer.balance == 1_000  # unchanged (cannot afford)
    assert employee.balance == 1_000  # unchanged


@pytest.mark.anyio
async def test_process_multiple_contracts(db_session: AsyncSession):
    """Multiple active contracts should all be processed."""
    employer = await _make_agent(db_session, name="BigCorp", balance=100_000)
    emp1 = await _make_agent(db_session, name="Worker1", balance=0)
    emp2 = await _make_agent(db_session, name="Worker2", balance=0)

    for employee, salary in [(emp1, 3_000), (emp2, 5_000)]:
        contract = Contract(
            id=uuid4(),
            contract_type=ContractType.EMPLOYMENT,
            party_a_id=employer.id,
            party_b_id=employee.id,
            status=ContractStatus.ACTIVE,
            tick_start=0,
            terms={},
            payment_amount=salary,
            payment_interval_ticks=10,
        )
        db_session.add(contract)
    await db_session.flush()

    payments = await _process_contract_payments(db_session, tick=10)
    await db_session.flush()

    assert payments == 2
    assert employer.balance == 100_000 - 3_000 - 5_000  # 92_000
    assert emp1.balance == 3_000
    assert emp2.balance == 5_000


# =====================================================================
# Macro/Micro Tick Engine Tests
# =====================================================================

from agentburg_server.engine.tick import TIME_OF_DAY_CYCLE, TickEngine


class TestTimeOfDay:
    """Test time_of_day property cycling."""

    def test_initial_time_is_morning(self):
        engine = TickEngine()
        assert engine.time_of_day == "morning"

    def test_cycle_through_full_day(self):
        engine = TickEngine()
        actual = []
        for i in range(len(TIME_OF_DAY_CYCLE)):
            engine.tick = i
            actual.append(engine.time_of_day)
        assert actual == list(TIME_OF_DAY_CYCLE)

    def test_cycle_wraps_around(self):
        engine = TickEngine()
        engine.tick = len(TIME_OF_DAY_CYCLE)
        assert engine.time_of_day == "morning"

    def test_afternoon_at_tick_2(self):
        engine = TickEngine()
        engine.tick = 2
        assert engine.time_of_day == "afternoon"

    def test_night_at_tick_5(self):
        engine = TickEngine()
        engine.tick = 5
        assert engine.time_of_day == "night"


class TestDayCounter:
    """Test day counter incrementing."""

    def test_initial_day_is_zero(self):
        engine = TickEngine()
        assert engine.day == 0

    def test_day_increments_after_full_cycle(self):
        engine = TickEngine()
        engine.ticks_per_day = 6
        for _ in range(6):
            engine.tick += 1
            if engine.tick % engine.ticks_per_day == 0:
                engine.day += 1
        assert engine.day == 1

    def test_three_full_days(self):
        engine = TickEngine()
        engine.ticks_per_day = 6
        for _ in range(18):
            engine.tick += 1
            if engine.tick % engine.ticks_per_day == 0:
                engine.day += 1
        assert engine.day == 3


class TestTickEngineConfig:
    """Test tick engine configuration."""

    def test_default_values(self):
        engine = TickEngine()
        assert engine.tick == 0
        assert engine.micro_tick == 0
        assert engine.running is False

    def test_micros_per_macro(self):
        engine = TickEngine()
        engine.macro_interval = 600.0
        engine.micro_interval = 30.0
        assert int(engine.macro_interval / engine.micro_interval) == 20

    def test_micros_per_macro_short(self):
        engine = TickEngine()
        engine.macro_interval = 30.0
        engine.micro_interval = 5.0
        assert int(engine.macro_interval / engine.micro_interval) == 6


class TestTickEngineLifecycle:
    """Test engine start/stop."""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        engine = TickEngine()
        engine.macro_interval = 9999
        engine.micro_interval = 9999
        await engine.start()
        assert engine.running is True
        await engine.stop()
        assert engine.running is False

    @pytest.mark.asyncio
    async def test_double_start_safe(self):
        engine = TickEngine()
        engine.macro_interval = 9999
        engine.micro_interval = 9999
        await engine.start()
        await engine.start()
        assert engine.running is True
        await engine.stop()
