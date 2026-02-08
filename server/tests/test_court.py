"""Tests for the court service — filing lawsuits, processing verdicts, determinism."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.social import CaseStatus, CaseType
from agentburg_server.services.court import file_lawsuit, process_pending_cases

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_agent(
    session: AsyncSession,
    *,
    name: str = "CourtAgent",
    balance: int = 10_000,
    reputation: int = 500,
    credit_score: int = 700,
) -> Agent:
    """Insert and return a fresh Agent for court tests."""
    agent = Agent(
        id=uuid4(),
        name=name,
        api_token_hash=sha256(f"token-{name}-{uuid4()}".encode()).hexdigest(),
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=balance,
        inventory={},
        location="downtown",
        reputation=reputation,
        credit_score=credit_score,
    )
    session.add(agent)
    await session.flush()
    return agent


# ---------------------------------------------------------------------------
# Filing lawsuits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_file_lawsuit(db_session: AsyncSession):
    """Filing a lawsuit should deduct the filing fee and create a case record."""
    plaintiff = await _make_agent(db_session, name="Plaintiff", balance=5_000)
    defendant = await _make_agent(db_session, name="Defendant", balance=5_000)

    case = await file_lawsuit(
        db_session,
        plaintiff_id=plaintiff.id,
        defendant_id=defendant.id,
        case_type=CaseType.FRAUD,
        description="Sold counterfeit goods",
        evidence={"receipt": "R-001", "witness": "Agent-X"},
        tick=10,
    )
    await db_session.flush()

    assert case.status == CaseStatus.FILED
    assert case.case_type == CaseType.FRAUD
    assert case.plaintiff_id == plaintiff.id
    assert case.defendant_id == defendant.id
    assert case.tick_filed == 10
    # Filing fee = 500
    assert plaintiff.balance == 5_000 - 500


@pytest.mark.anyio
async def test_file_lawsuit_self(db_session: AsyncSession):
    """Filing a lawsuit against oneself must raise ValueError."""
    agent = await _make_agent(db_session, name="SelfSuer", balance=5_000)

    with pytest.raises(ValueError, match="Cannot sue yourself"):
        await file_lawsuit(
            db_session,
            plaintiff_id=agent.id,
            defendant_id=agent.id,
            case_type=CaseType.OTHER,
            description="Suing myself",
            evidence={},
            tick=1,
        )


@pytest.mark.anyio
async def test_file_lawsuit_insufficient_balance(db_session: AsyncSession):
    """Filing a lawsuit without enough balance for the filing fee should fail."""
    plaintiff = await _make_agent(db_session, name="BrokePlaintiff", balance=100)
    defendant = await _make_agent(db_session, name="Defendant2", balance=5_000)

    with pytest.raises(ValueError, match="Insufficient balance"):
        await file_lawsuit(
            db_session,
            plaintiff_id=plaintiff.id,
            defendant_id=defendant.id,
            case_type=CaseType.THEFT,
            description="Stole my items",
            evidence={},
            tick=1,
        )


# ---------------------------------------------------------------------------
# Processing pending cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_process_pending_cases(db_session: AsyncSession):
    """Cases filed at least 3 ticks ago should be processed to a verdict."""
    plaintiff = await _make_agent(db_session, name="P1", balance=10_000, reputation=600)
    defendant = await _make_agent(db_session, name="D1", balance=10_000, reputation=400)

    await file_lawsuit(
        db_session,
        plaintiff_id=plaintiff.id,
        defendant_id=defendant.id,
        case_type=CaseType.BREACH_OF_CONTRACT,
        description="Failed to deliver goods",
        evidence={"contract": "C-001", "delivery_log": "empty", "chat_log": "proof"},
        tick=1,
    )
    await db_session.flush()

    # Process at tick=4 (3 ticks after filing at tick=1)
    resolved = await process_pending_cases(db_session, tick=4)
    await db_session.flush()

    assert len(resolved) == 1
    resolved_case = resolved[0]
    assert resolved_case.tick_resolved == 4
    assert resolved_case.status in (
        CaseStatus.VERDICT_GUILTY,
        CaseStatus.VERDICT_NOT_GUILTY,
    )
    assert resolved_case.verdict_details is not None


@pytest.mark.anyio
async def test_process_pending_cases_too_early(db_session: AsyncSession):
    """Cases filed less than 3 ticks ago should NOT be processed."""
    plaintiff = await _make_agent(db_session, name="P2", balance=10_000)
    defendant = await _make_agent(db_session, name="D2", balance=10_000)

    await file_lawsuit(
        db_session,
        plaintiff_id=plaintiff.id,
        defendant_id=defendant.id,
        case_type=CaseType.DEFAMATION,
        description="Spread false rumors",
        evidence={"screenshot": "img-001"},
        tick=5,
    )
    await db_session.flush()

    # Process at tick=7 (only 2 ticks after filing — need 3)
    resolved = await process_pending_cases(db_session, tick=7)

    assert len(resolved) == 0


# ---------------------------------------------------------------------------
# Verdict determinism
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verdict_determinism(db_session: AsyncSession):
    """The same tick + case_id must always produce the same verdict.

    The court service uses ``hashlib.sha256(f"{tick}:{case.id}")`` for
    deterministic randomness, so calling process twice with the same
    inputs should yield identical results.
    """
    plaintiff = await _make_agent(db_session, name="DetP", balance=10_000, reputation=500)
    defendant = await _make_agent(db_session, name="DetD", balance=10_000, reputation=500)

    case = await file_lawsuit(
        db_session,
        plaintiff_id=plaintiff.id,
        defendant_id=defendant.id,
        case_type=CaseType.PROPERTY_DISPUTE,
        description="Boundary dispute",
        evidence={"survey": "S-001"},
        tick=1,
    )
    await db_session.flush()

    # Manually compute the expected verdict using the same algorithm
    import hashlib

    verdict_seed = hashlib.sha256(f"4:{case.id}".encode()).digest()
    verdict_roll = (int.from_bytes(verdict_seed[:4], "big") % 100) + 1

    # Evidence: 1 item -> score = 10; reputation diff = 0
    # win_prob = 50 + min(10,30) + max(min(0//10,20),-20) = 50 + 10 + 0 = 60
    # win_prob clamped to [10,90] -> 60
    expected_guilty = verdict_roll <= 60

    # Process the case
    resolved = await process_pending_cases(db_session, tick=4)
    await db_session.flush()

    assert len(resolved) == 1
    actual_guilty = resolved[0].status == CaseStatus.VERDICT_GUILTY
    assert actual_guilty == expected_guilty


@pytest.mark.anyio
async def test_guilty_verdict_consequences(db_session: AsyncSession):
    """A guilty verdict should fine the defendant and adjust reputations.

    We construct a scenario heavily biased toward a guilty verdict
    (lots of evidence, high plaintiff reputation, low defendant reputation)
    so that the deterministic hash is very likely to produce guilty.
    We also verify the numeric consequences if guilty.
    """
    plaintiff = await _make_agent(
        db_session, name="StrongP", balance=10_000, reputation=900
    )
    defendant = await _make_agent(
        db_session, name="WeakD", balance=10_000, reputation=100, credit_score=500
    )

    # Many evidence items to push win_probability near 90
    evidence = {f"evidence_{i}": f"item_{i}" for i in range(10)}

    await file_lawsuit(
        db_session,
        plaintiff_id=plaintiff.id,
        defendant_id=defendant.id,
        case_type=CaseType.FRAUD,
        description="Large-scale fraud operation",
        evidence=evidence,
        tick=1,
    )
    await db_session.flush()

    p_balance_before = plaintiff.balance
    d_balance_before = defendant.balance
    d_rep_before = defendant.reputation
    d_credit_before = defendant.credit_score

    resolved = await process_pending_cases(db_session, tick=4)
    await db_session.flush()

    assert len(resolved) == 1
    verdict = resolved[0]

    if verdict.status == CaseStatus.VERDICT_GUILTY:
        # Fine should be calculated: base=5000 for FRAUD, capped at 30% of defendant balance
        from agentburg_server.services.court import _calculate_fine

        expected_fine = _calculate_fine(CaseType.FRAUD, d_balance_before)
        assert verdict.fine_amount == expected_fine

        # Defendant pays the fine, plaintiff receives it
        assert defendant.balance == d_balance_before - expected_fine
        assert plaintiff.balance == p_balance_before + expected_fine

        # Reputation changes
        assert defendant.reputation == max(0, d_rep_before - 50)
        assert defendant.credit_score == max(0, d_credit_before - 30)
    else:
        # Not guilty: defendant reputation increases slightly
        assert defendant.reputation == min(1000, d_rep_before + 5)
