"""Social models — court cases, contracts, businesses."""

import enum
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentburg_server.models.base import Base, TimestampMixin, UUIDMixin

# --- Court Cases ---


class CaseStatus(enum.StrEnum):
    FILED = "filed"
    IN_PROGRESS = "in_progress"
    VERDICT_GUILTY = "verdict_guilty"
    VERDICT_NOT_GUILTY = "verdict_not_guilty"
    SETTLED = "settled"
    DISMISSED = "dismissed"


class CaseType(enum.StrEnum):
    FRAUD = "fraud"
    BREACH_OF_CONTRACT = "breach_of_contract"
    THEFT = "theft"
    DEFAMATION = "defamation"
    PROPERTY_DISPUTE = "property_dispute"
    ANTITRUST = "antitrust"
    OTHER = "other"


class CourtCase(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "court_cases"

    case_type: Mapped[CaseType] = mapped_column(SAEnum(CaseType), nullable=False)
    plaintiff_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    defendant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[CaseStatus] = mapped_column(
        SAEnum(CaseStatus), default=CaseStatus.FILED, nullable=False
    )
    verdict_details: Mapped[str | None] = mapped_column(Text)
    fine_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tick_filed: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_resolved: Mapped[int | None] = mapped_column(Integer)


# --- Contracts ---


class ContractType(enum.StrEnum):
    EMPLOYMENT = "employment"
    SUPPLY = "supply"
    PARTNERSHIP = "partnership"
    LEASE = "lease"
    CUSTOM = "custom"


class ContractStatus(enum.StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    COMPLETED = "completed"
    BREACHED = "breached"
    TERMINATED = "terminated"


class Contract(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "contracts"

    contract_type: Mapped[ContractType] = mapped_column(SAEnum(ContractType), nullable=False)
    party_a_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    party_b_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    terms: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[ContractStatus] = mapped_column(
        SAEnum(ContractStatus), default=ContractStatus.PROPOSED, nullable=False
    )
    payment_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payment_interval_ticks: Mapped[int | None] = mapped_column(Integer)
    tick_start: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_end: Mapped[int | None] = mapped_column(Integer)


# --- Businesses ---


class BusinessType(enum.StrEnum):
    SHOP = "shop"
    FACTORY = "factory"
    FARM = "farm"
    BANK = "bank"
    RESTAURANT = "restaurant"
    SERVICE = "service"
    CUSTOM = "custom"


class Business(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "businesses"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_type: Mapped[BusinessType] = mapped_column(SAEnum(BusinessType), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True
    )
    owner: Mapped["Agent"] = relationship(back_populates="businesses")  # noqa: F821
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    capital: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revenue: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expenses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    employees: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    products: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # item → price
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
