"""Court service — filing lawsuits, processing verdicts, enforcing fines."""

import hashlib
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent, AgentStatus
from agentburg_server.models.event import EventCategory, WorldEventLog
from agentburg_server.models.social import CaseStatus, CaseType, CourtCase

logger = logging.getLogger(__name__)


async def file_lawsuit(
    session: AsyncSession,
    plaintiff_id: UUID,
    defendant_id: UUID,
    case_type: CaseType,
    description: str,
    evidence: dict,
    tick: int,
) -> CourtCase:
    """File a lawsuit against another agent."""
    plaintiff = await session.get(Agent, plaintiff_id)
    defendant = await session.get(Agent, defendant_id)

    if plaintiff is None or defendant is None:
        raise ValueError("Plaintiff or defendant not found")
    if plaintiff_id == defendant_id:
        raise ValueError("Cannot sue yourself")

    # Filing fee: 500 cents ($5.00)
    filing_fee = 500
    if plaintiff.balance < filing_fee:
        raise ValueError("Insufficient balance for filing fee")
    plaintiff.balance -= filing_fee

    case = CourtCase(
        case_type=case_type,
        plaintiff_id=plaintiff_id,
        defendant_id=defendant_id,
        description=description,
        evidence=evidence,
        status=CaseStatus.FILED,
        tick_filed=tick,
    )
    session.add(case)

    await _log_event(
        session,
        tick=tick,
        category=EventCategory.COURT,
        event_type="lawsuit_filed",
        agent_id=plaintiff_id,
        target_id=defendant_id,
        description=f"Lawsuit filed: {case_type.value} - {description[:100]}",
        data={"case_type": case_type.value},
    )

    return case


async def process_pending_cases(session: AsyncSession, tick: int) -> list[CourtCase]:
    """Process cases that have been filed for enough time (3 ticks minimum deliberation)."""
    stmt = select(CourtCase).where(
        CourtCase.status == CaseStatus.FILED,
        CourtCase.tick_filed <= tick - 3,  # 3 tick deliberation period
    )
    result = await session.execute(stmt)
    cases = list(result.scalars().all())

    resolved: list[CourtCase] = []
    for case in cases:
        case.status = CaseStatus.IN_PROGRESS

        plaintiff = await session.get(Agent, case.plaintiff_id)
        defendant = await session.get(Agent, case.defendant_id)
        if plaintiff is None or defendant is None:
            case.status = CaseStatus.DISMISSED
            case.tick_resolved = tick
            resolved.append(case)
            continue

        # Simple verdict logic based on evidence quality and reputation
        evidence_score = len(case.evidence) * 10  # More evidence = stronger case
        reputation_diff = plaintiff.reputation - defendant.reputation

        # Base probability of winning: 50% + evidence bonus + reputation bonus
        win_probability = 50 + min(evidence_score, 30) + max(min(reputation_diff // 10, 20), -20)
        win_probability = max(10, min(90, win_probability))

        # Deterministic verdict using hash of tick + case ID for reproducibility
        verdict_seed = hashlib.sha256(f"{tick}:{case.id}".encode()).digest()
        verdict_roll = (int.from_bytes(verdict_seed[:4], "big") % 100) + 1
        guilty = verdict_roll <= win_probability

        if guilty:
            case.status = CaseStatus.VERDICT_GUILTY
            # Fine proportional to case severity
            fine = _calculate_fine(case.case_type, defendant.balance)
            case.fine_amount = fine

            defendant.balance -= fine
            plaintiff.balance += fine  # Compensation goes to plaintiff

            # Reputation consequences
            defendant.reputation = max(0, defendant.reputation - 50)
            plaintiff.reputation = min(1000, plaintiff.reputation + 10)

            # Credit score impact
            defendant.credit_score = max(0, defendant.credit_score - 30)

            # Update stats
            plaintiff.lawsuits_won += 1
            defendant.lawsuits_lost += 1

            # Jail for serious crimes
            if case.case_type in (CaseType.FRAUD, CaseType.THEFT) and fine > 5000:
                defendant.status = AgentStatus.JAILED

            case.verdict_details = f"Guilty. Fine: {fine} cents."
        else:
            case.status = CaseStatus.VERDICT_NOT_GUILTY
            case.verdict_details = "Not guilty. Case dismissed."
            defendant.reputation = min(1000, defendant.reputation + 5)
            plaintiff.lawsuits_lost += 1

        case.tick_resolved = tick
        resolved.append(case)

        await _log_event(
            session,
            tick=tick,
            category=EventCategory.COURT,
            event_type="verdict",
            agent_id=case.plaintiff_id,
            target_id=case.defendant_id,
            description=f"Verdict: {case.status.value} - {case.verdict_details}",
            data={
                "case_type": case.case_type.value,
                "guilty": guilty,
                "fine": case.fine_amount,
            },
        )

    logger.info("Tick %d: resolved %d court cases", tick, len(resolved))
    return resolved


def _calculate_fine(case_type: CaseType, defendant_balance: int) -> int:
    """Calculate fine based on case type and defendant's means."""
    base_fines = {
        CaseType.FRAUD: 5000,
        CaseType.THEFT: 3000,
        CaseType.BREACH_OF_CONTRACT: 2000,
        CaseType.DEFAMATION: 1000,
        CaseType.PROPERTY_DISPUTE: 2500,
        CaseType.ANTITRUST: 10000,
        CaseType.OTHER: 1000,
    }
    base = base_fines.get(case_type, 1000)

    # Cap at 30% of defendant's balance to avoid immediate bankruptcy
    max_fine = max(defendant_balance * 30 // 100, 500)
    return min(base, max_fine)


async def _log_event(
    session: AsyncSession,
    tick: int,
    category: EventCategory,
    event_type: str,
    description: str,
    agent_id: UUID | None = None,
    target_id: UUID | None = None,
    data: dict | None = None,
) -> None:
    event = WorldEventLog(
        tick=tick,
        category=category,
        event_type=event_type,
        agent_id=agent_id,
        target_id=target_id,
        description=description,
        data=data or {},
    )
    session.add(event)
