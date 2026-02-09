"""Initial schema — all tables for AgentBurg world state.

Revision ID: 001
Revises:
Create Date: 2026-02-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Users ---
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("username", sa.String(50), unique=True, nullable=False, index=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("is_admin", sa.Boolean, default=False, nullable=False),
        sa.Column("max_agents", sa.Integer, default=3, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- Agents ---
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("title", sa.String(100), nullable=True),
        sa.Column("bio", sa.Text, nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("api_token_hash", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "tier", sa.Enum("player", "npc_llm", "npc_rule", name="agenttier"), nullable=False, server_default="player"
        ),
        sa.Column(
            "status",
            sa.Enum("active", "sleeping", "bankrupt", "jailed", "suspended", name="agentstatus"),
            nullable=False,
            server_default="active",
            index=True,
        ),
        sa.Column("balance", sa.Integer, default=10000, nullable=False),
        sa.Column("inventory", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("location", sa.String(100), default="town_center", nullable=False),
        sa.Column("reputation", sa.Integer, default=500, nullable=False),
        sa.Column("credit_score", sa.Integer, default=500, nullable=False),
        sa.Column("total_trades", sa.Integer, default=0, nullable=False),
        sa.Column("total_earnings", sa.Integer, default=0, nullable=False),
        sa.Column("total_losses", sa.Integer, default=0, nullable=False),
        sa.Column("lawsuits_won", sa.Integer, default=0, nullable=False),
        sa.Column("lawsuits_lost", sa.Integer, default=0, nullable=False),
        sa.Column("is_connected", sa.Boolean, default=False, nullable=False),
        sa.Column("last_seen_tick", sa.Integer, default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- Accounts ---
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False, index=True),
        sa.Column(
            "account_type",
            sa.Enum("checking", "savings", "loan", name="accounttype"),
            nullable=False,
            server_default="checking",
        ),
        sa.Column("balance", sa.Integer, default=0, nullable=False),
        sa.Column("interest_rate", sa.Integer, default=300, nullable=False),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("balance >= 0 OR account_type = 'loan'", name="ck_account_balance"),
    )

    # --- Market Orders ---
    op.create_table(
        "market_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False, index=True),
        sa.Column("item", sa.String(100), nullable=False),
        sa.Column("side", sa.Enum("buy", "sell", name="orderside"), nullable=False),
        sa.Column("price", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("filled_quantity", sa.Integer, default=0, nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "filled", "partially_filled", "cancelled", "expired", name="orderstatus"),
            nullable=False,
            server_default="open",
        ),
        sa.Column("tick_created", sa.Integer, nullable=False),
        sa.Column("tick_expires", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_order_quantity_positive"),
        sa.CheckConstraint("price > 0", name="ck_order_price_positive"),
    )
    op.create_index("ix_orders_matching", "market_orders", ["item", "side", "status", "price"])

    # --- Trades ---
    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tick", sa.Integer, nullable=False),
        sa.Column("item", sa.String(100), nullable=False),
        sa.Column("buyer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("price", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("total", sa.Integer, nullable=False),
        sa.Column("buy_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("market_orders.id"), nullable=False),
        sa.Column("sell_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("market_orders.id"), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_trades_tick", "trades", ["tick"])
    op.create_index("ix_trades_item", "trades", ["item"])

    # --- Properties ---
    op.create_table(
        "properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "property_type",
            sa.Enum("land", "building", "shop", "factory", "house", name="propertytype"),
            nullable=False,
        ),
        sa.Column("location", sa.String(100), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True, index=True),
        sa.Column("market_value", sa.Integer, default=0, nullable=False),
        sa.Column("is_for_sale", sa.Boolean, default=False, nullable=False),
        sa.Column("asking_price", sa.Integer, nullable=True),
        sa.Column("metadata", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- Court Cases ---
    op.create_table(
        "court_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_type",
            sa.Enum(
                "fraud",
                "breach_of_contract",
                "theft",
                "defamation",
                "property_dispute",
                "antitrust",
                "other",
                name="casetype",
            ),
            nullable=False,
        ),
        sa.Column("plaintiff_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("defendant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("evidence", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "filed",
                "in_progress",
                "verdict_guilty",
                "verdict_not_guilty",
                "settled",
                "dismissed",
                name="casestatus",
            ),
            nullable=False,
            server_default="filed",
        ),
        sa.Column("verdict_details", sa.Text, nullable=True),
        sa.Column("fine_amount", sa.Integer, default=0, nullable=False),
        sa.Column("tick_filed", sa.Integer, nullable=False),
        sa.Column("tick_resolved", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- Contracts ---
    op.create_table(
        "contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "contract_type",
            sa.Enum("employment", "supply", "partnership", "lease", "custom", name="contracttype"),
            nullable=False,
        ),
        sa.Column("party_a_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("party_b_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("terms", postgresql.JSONB, nullable=False),
        sa.Column(
            "status",
            sa.Enum("proposed", "active", "completed", "breached", "terminated", name="contractstatus"),
            nullable=False,
            server_default="proposed",
        ),
        sa.Column("payment_amount", sa.Integer, default=0, nullable=False),
        sa.Column("payment_interval_ticks", sa.Integer, nullable=True),
        sa.Column("tick_start", sa.Integer, nullable=False),
        sa.Column("tick_end", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- Businesses ---
    op.create_table(
        "businesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "business_type",
            sa.Enum("shop", "factory", "farm", "bank", "restaurant", "service", "custom", name="businesstype"),
            nullable=False,
        ),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False, index=True),
        sa.Column("location", sa.String(100), nullable=False),
        sa.Column("capital", sa.Integer, default=0, nullable=False),
        sa.Column("revenue", sa.Integer, default=0, nullable=False),
        sa.Column("expenses", sa.Integer, default=0, nullable=False),
        sa.Column("employees", sa.Integer, default=0, nullable=False),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("products", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("metadata", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- World Events ---
    op.create_table(
        "world_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tick", sa.Integer, nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "trade",
                "bank",
                "property",
                "court",
                "contract",
                "business",
                "social",
                "system",
                "crime",
                name="eventcategory",
            ),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("data", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_events_tick_cat", "world_events", ["tick", "category"])
    op.create_index("ix_events_agent", "world_events", ["agent_id"])


def downgrade() -> None:
    op.drop_table("world_events")
    op.drop_table("businesses")
    op.drop_table("contracts")
    op.drop_table("court_cases")
    op.drop_table("properties")
    op.drop_table("trades")
    op.drop_table("market_orders")
    op.drop_table("accounts")
    op.drop_table("agents")
    op.drop_table("users")

    # Drop enums
    for name in [
        "eventcategory",
        "businesstype",
        "contractstatus",
        "contracttype",
        "casestatus",
        "casetype",
        "propertytype",
        "orderstatus",
        "orderside",
        "accounttype",
        "agentstatus",
        "agenttier",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name}")
