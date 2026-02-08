# AgentBurg Security Architecture

> **Version**: 1.0.0
> **Status**: Design Phase
> **Last Updated**: 2026-02-09
> **Author**: Security Engineering Team

---

## 목차

1. [위협 모델 개요](#1-위협-모델-개요)
2. [인증 및 인가 (Authentication & Authorization)](#2-인증-및-인가)
3. [경제 시스템 보안 (Economic Security)](#3-경제-시스템-보안)
4. [에이전트 행동 검증 (Agent Action Validation)](#4-에이전트-행동-검증)
5. [남용 방지 (Abuse Prevention)](#5-남용-방지)
6. [인프라 보안 (Infrastructure Security)](#6-인프라-보안)
7. [모니터링 및 사고 대응 (Monitoring & Incident Response)](#7-모니터링-및-사고-대응)
8. [개인정보 보호 (Privacy)](#8-개인정보-보호)
9. [보안 체크리스트](#9-보안-체크리스트)

---

## 1. 위협 모델 개요

### 1.1 시스템 경계

AgentBurg는 **서버(Open World)**와 **클라이언트(Agent Brain)** 간 명확한 신뢰 경계가 존재한다.

```
┌─────────────────────────────────────────────────────┐
│                  TRUST BOUNDARY                      │
│                                                      │
│  ┌──────────────┐         ┌──────────────────────┐  │
│  │  Cloud Server │◄──WSS──►│  Local Agent Brain   │  │
│  │  (Trusted)    │         │  (UNTRUSTED)         │  │
│  │               │         │                      │  │
│  │  - World State│         │  - User's LLM        │  │
│  │  - Economy    │         │  - Agent Personality  │  │
│  │  - Validation │         │  - Decision Making    │  │
│  │  - Audit Log  │         │  - API Keys (local)   │  │
│  └──────────────┘         └──────────────────────┘  │
│                                                      │
└─────────────────────────────────────────────────────┘
```

**핵심 원칙: 클라이언트를 절대 신뢰하지 않는다.** 모든 클라이언트 입력은 악의적일 수 있다고 가정한다.

### 1.2 위협 행위자 (Threat Actors)

| 행위자 | 동기 | 능력 | 위험도 |
|--------|------|------|--------|
| **악의적 사용자** | 무료 자원 착취, 경제 조작 | WebSocket 프로토콜 조작, 다수 에이전트 생성 | **HIGH** |
| **자동화 봇** | 시빌 공격, 마켓 조작 | 대량 계정 생성, API 자동화 | **HIGH** |
| **경쟁 에이전트** | 타 에이전트 방해, 불공정 이득 | 프로토콜 내 합법적 행동의 극단적 사용 | **MEDIUM** |
| **외부 공격자** | DDoS, 데이터 탈취 | 네트워크 공격, 취약점 익스플로잇 | **MEDIUM** |
| **내부자** | 데이터 유출, 권한 남용 | 시스템 접근 권한 보유 | **LOW** |

### 1.3 STRIDE 분석

| 위협 | 대상 | 대응 |
|------|------|------|
| **S**poofing | 에이전트/사용자 ID | JWT + per-agent token |
| **T**ampering | 거래 데이터, 잔고 | SERIALIZABLE isolation + 감사 로그 |
| **R**epudiation | 에이전트 행동 부인 | 불변 감사 로그 + 이벤트 소싱 |
| **I**nformation Disclosure | 타 에이전트 전략, 사용자 정보 | 접근 제어 + 데이터 최소화 |
| **D**enial of Service | WebSocket 서버, DB | 레이트 리밋 + 백프레셔 |
| **E**levation of Privilege | 관리자 기능 접근 | RBAC + 최소 권한 원칙 |

---

## 2. 인증 및 인가

### 2.1 사용자 등록 흐름

두 가지 등록 경로를 지원한다: 이메일/비밀번호와 OAuth 2.0.

```
┌──────┐     ┌──────────┐     ┌──────────┐     ┌─────────┐
│Client│────►│ /register │────►│ Validate  │────►│ Create  │
│      │     │           │     │ + Hash PW │     │ Account │
└──────┘     └──────────┘     └──────────┘     └─────────┘
                                                     │
                                                     ▼
                                              ┌─────────────┐
                                              │Send Verify  │
                                              │Email (6-digit│
                                              │OTP, 10min)  │
                                              └─────────────┘
```

**OAuth 흐름 (Google, GitHub, Discord)**:

```
Client ──► /auth/oauth/{provider} ──► Provider ──► Callback ──► Create/Link Account
```

#### 비밀번호 정책 및 해싱

```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Argon2id — winner of Password Hashing Competition
# Memory: 64MB, Iterations: 3, Parallelism: 4
ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,   # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=argon2.Type.ID,  # Argon2id — hybrid of Argon2i and Argon2d
)


class UserService:
    PASSWORD_MIN_LENGTH = 12
    PASSWORD_MAX_LENGTH = 128  # Prevent DoS via extremely long passwords

    def hash_password(self, password: str) -> str:
        """Hash password with Argon2id. Salt is auto-generated."""
        if len(password) < self.PASSWORD_MIN_LENGTH:
            raise ValueError("Password must be at least 12 characters")
        if len(password) > self.PASSWORD_MAX_LENGTH:
            raise ValueError("Password must be at most 128 characters")
        return ph.hash(password)

    def verify_password(self, stored_hash: str, password: str) -> bool:
        """Verify password; auto-rehash if parameters changed."""
        try:
            ph.verify(stored_hash, password)
            if ph.check_needs_rehash(stored_hash):
                return True  # Caller should update stored hash
            return True
        except VerifyMismatchError:
            return False
```

### 2.2 토큰 시스템 아키텍처

AgentBurg는 **3-레이어 토큰 시스템**을 사용한다.

```
Layer 1: User Access Token  (JWT, 15min TTL)
Layer 2: User Refresh Token (Opaque, 7day TTL, DB-stored)
Layer 3: Agent Token         (Opaque, per-agent, revocable, scoped)
```

#### JWT 구현

```python
import jwt
import secrets
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from enum import StrEnum


class Role(StrEnum):
    USER = "user"
    AGENT = "agent"
    ADMIN = "admin"
    SPECTATOR = "spectator"


class TokenPayload(BaseModel):
    sub: str          # user_id (UUID)
    role: Role
    iat: int
    exp: int
    jti: str          # unique token ID for revocation
    iss: str = "agentburg"
    aud: str = "agentburg-api"


class TokenService:
    def __init__(self, secret_key: str, algorithm: str = "HS256"):
        # In production, use RS256 with key rotation
        self._secret = secret_key
        self._algo = algorithm

    def create_access_token(
        self,
        user_id: str,
        role: Role,
        ttl: timedelta = timedelta(minutes=15),
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = TokenPayload(
            sub=user_id,
            role=role,
            iat=int(now.timestamp()),
            exp=int((now + ttl).timestamp()),
            jti=secrets.token_urlsafe(16),
        )
        return jwt.encode(payload.model_dump(), self._secret, algorithm=self._algo)

    def create_refresh_token(self) -> str:
        """Generate opaque refresh token stored in DB."""
        return secrets.token_urlsafe(64)

    def verify_access_token(self, token: str) -> TokenPayload:
        """Verify and decode JWT. Raises jwt.InvalidTokenError on failure."""
        data = jwt.decode(
            token,
            self._secret,
            algorithms=[self._algo],
            issuer="agentburg",
            audience="agentburg-api",
            options={"require": ["sub", "role", "exp", "iat", "jti"]},
        )
        return TokenPayload(**data)
```

#### Refresh Token 스키마

```sql
CREATE TABLE refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  BYTEA NOT NULL,           -- SHA-256 hash of the token
    device_info TEXT,                       -- User-Agent fingerprint
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ,               -- NULL = active
    CONSTRAINT  idx_refresh_token_hash UNIQUE (token_hash)
);

-- Auto-cleanup expired tokens
CREATE INDEX idx_refresh_tokens_expires ON refresh_tokens (expires_at)
    WHERE revoked_at IS NULL;
```

**Refresh Token Rotation**: 매번 refresh 시 이전 토큰을 폐기하고 새 토큰을 발급한다. 이전 토큰 재사용 시도가 감지되면 해당 사용자의 **모든** refresh token을 즉시 폐기한다 (token theft 감지).

### 2.3 에이전트 토큰 시스템

각 에이전트는 고유한 스코프 토큰을 받는다.

```python
class AgentTokenScope(StrEnum):
    """Scoped permissions for agent tokens."""
    TRADE = "trade"         # Buy/sell on market
    BANK = "bank"           # Bank transactions
    CHAT = "chat"           # Agent-to-agent communication
    MOVE = "move"           # Move in the world
    WORK = "work"           # Perform jobs
    READ_WORLD = "read_world"  # Read world state


class AgentToken(BaseModel):
    id: str                           # UUID
    user_id: str                      # Owner user
    agent_id: str                     # Target agent
    token_hash: str                   # SHA-256 hash (never store plaintext)
    scopes: list[AgentTokenScope]     # Allowed actions
    created_at: datetime
    expires_at: datetime              # Max 30 days
    last_used_at: datetime | None
    revoked: bool = False
    ip_whitelist: list[str] | None    # Optional IP restriction
```

```sql
CREATE TABLE agent_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    token_hash      BYTEA NOT NULL UNIQUE,
    scopes          TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    last_used_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    ip_whitelist    INET[],
    revocation_reason TEXT,
    CONSTRAINT chk_scopes_not_empty CHECK (array_length(scopes, 1) > 0)
);

CREATE INDEX idx_agent_tokens_agent ON agent_tokens (agent_id)
    WHERE revoked_at IS NULL;
```

### 2.4 WebSocket 인증 핸드셰이크

WebSocket은 HTTP 헤더를 통한 인증이 제한적이므로, **연결 초기화 시 인증 메시지**를 사용한다.

```python
import asyncio
from fastapi import WebSocket, WebSocketDisconnect


AUTH_TIMEOUT_SECONDS = 5  # Must authenticate within 5 seconds


async def websocket_auth_handshake(
    ws: WebSocket,
    token_service: TokenService,
) -> TokenPayload | None:
    """
    WebSocket authentication handshake.
    Client must send auth message within AUTH_TIMEOUT_SECONDS.
    Returns TokenPayload on success, None on failure.
    """
    await ws.accept()

    try:
        # Wait for auth message with timeout
        raw = await asyncio.wait_for(
            ws.receive_json(),
            timeout=AUTH_TIMEOUT_SECONDS,
        )

        if raw.get("type") != "auth":
            await ws.close(code=4001, reason="Expected auth message")
            return None

        token = raw.get("token", "")
        agent_token = raw.get("agent_token", "")

        # Verify user JWT
        payload = token_service.verify_access_token(token)

        # Verify agent token if provided (agent connection)
        if agent_token:
            await _verify_agent_token(agent_token, payload.sub)

        await ws.send_json({"type": "auth_ok", "server_time": _utc_now_iso()})
        return payload

    except asyncio.TimeoutError:
        await ws.close(code=4002, reason="Auth timeout")
        return None
    except Exception:
        await ws.close(code=4003, reason="Auth failed")
        return None
```

**클라이언트 측 핸드셰이크**:

```json
// 1. Client connects to wss://api.agentburg.io/ws
// 2. Client sends auth message:
{
    "type": "auth",
    "token": "<user_jwt>",
    "agent_token": "<agent_specific_token>"
}
// 3. Server responds:
{
    "type": "auth_ok",
    "server_time": "2026-02-09T12:00:00Z"
}
// 4. Now agent can send action messages
```

### 2.5 역할 기반 접근 제어 (RBAC)

```python
from enum import IntFlag


class Permission(IntFlag):
    """Bitfield permissions for fine-grained access control."""
    NONE            = 0
    # Agent actions
    AGENT_TRADE     = 1 << 0    # 1
    AGENT_BANK      = 1 << 1    # 2
    AGENT_CHAT      = 1 << 2    # 4
    AGENT_MOVE      = 1 << 3    # 8
    AGENT_WORK      = 1 << 4    # 16
    # User actions
    USER_CREATE_AGENT = 1 << 8  # 256
    USER_VIEW_AGENTS  = 1 << 9  # 512
    USER_VIEW_MARKET  = 1 << 10 # 1024
    USER_VIEW_STATS   = 1 << 11 # 2048
    # Admin actions
    ADMIN_BAN_USER    = 1 << 16 # 65536
    ADMIN_BAN_AGENT   = 1 << 17
    ADMIN_VIEW_LOGS   = 1 << 18
    ADMIN_MODIFY_WORLD = 1 << 19
    ADMIN_MANAGE_USERS = 1 << 20


# Pre-defined role permission sets
ROLE_PERMISSIONS: dict[Role, Permission] = {
    Role.SPECTATOR: (
        Permission.USER_VIEW_MARKET | Permission.USER_VIEW_STATS
    ),
    Role.USER: (
        Permission.USER_CREATE_AGENT
        | Permission.USER_VIEW_AGENTS
        | Permission.USER_VIEW_MARKET
        | Permission.USER_VIEW_STATS
    ),
    Role.AGENT: (
        Permission.AGENT_TRADE
        | Permission.AGENT_BANK
        | Permission.AGENT_CHAT
        | Permission.AGENT_MOVE
        | Permission.AGENT_WORK
    ),
    Role.ADMIN: Permission(~0),  # All permissions
}


def check_permission(role: Role, required: Permission) -> bool:
    """Check if role has the required permission(s)."""
    return (ROLE_PERMISSIONS[role] & required) == required
```

---

## 3. 경제 시스템 보안

**이 섹션은 AgentBurg에서 가장 중요한 보안 영역이다.** 경제 무결성이 깨지면 전체 시뮬레이션이 의미를 잃는다.

### 3.1 핵심 원칙

1. **서버가 유일한 진실의 원천 (Single Source of Truth)**: 잔고, 소유권, 가격은 오직 서버 DB에서만 관리
2. **모든 경제 활동은 원자적 (Atomic)**: 부분 완료 상태는 존재할 수 없음
3. **낙관적 동시성은 금지**: 잔고 변경은 반드시 비관적 잠금(pessimistic lock) 사용
4. **정수 기반 연산**: 부동소수점 연산으로 인한 정밀도 손실 방지

### 3.2 화폐 표현

```python
from decimal import Decimal, ROUND_HALF_UP

# AgentBurg currency: "Burg" (₿)
# Internal representation: integer (cents/minor units)
# 1 Burg = 100 cents
# All DB columns use BIGINT for currency amounts

CURRENCY_SCALE = 100  # 1 Burg = 100 minor units
MAX_BALANCE = 10_000_000_00  # 10 million Burg (in cents) — world money supply cap
MIN_BALANCE = 0  # No negative balances allowed


class Money:
    """Immutable money type using integer arithmetic only."""

    __slots__ = ("_cents",)

    def __init__(self, cents: int):
        if not isinstance(cents, int):
            raise TypeError(f"Money requires int, got {type(cents).__name__}")
        if cents < 0:
            raise ValueError("Money cannot be negative")
        if cents > MAX_BALANCE:
            raise ValueError(f"Amount {cents} exceeds maximum {MAX_BALANCE}")
        self._cents = cents

    @classmethod
    def from_display(cls, amount: str) -> "Money":
        """Parse display amount like '42.50' into Money(4250)."""
        d = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return cls(int(d * CURRENCY_SCALE))

    @property
    def cents(self) -> int:
        return self._cents

    def __add__(self, other: "Money") -> "Money":
        result = self._cents + other._cents
        if result > MAX_BALANCE:
            raise OverflowError(f"Addition would exceed max balance: {result}")
        return Money(result)

    def __sub__(self, other: "Money") -> "Money":
        result = self._cents - other._cents
        if result < 0:
            raise ValueError("Subtraction would result in negative balance")
        return Money(result)

    def __repr__(self) -> str:
        return f"Money({self._cents})"
```

### 3.3 이중 지불 방지 (Double-Spend Prevention)

**PostgreSQL SERIALIZABLE isolation + SELECT ... FOR UPDATE** 조합을 사용한다.

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class TransactionService:
    """
    All economic mutations go through this service.
    Uses PostgreSQL SERIALIZABLE isolation with row-level locks.
    """

    MAX_RETRY_ATTEMPTS = 3  # Retry on serialization failure

    async def transfer(
        self,
        session: AsyncSession,
        from_agent_id: str,
        to_agent_id: str,
        amount: Money,
        reason: str,
    ) -> str:
        """
        Atomic transfer between two agents.
        Returns transaction_id on success.
        Raises InsufficientFunds or SerializationError.
        """
        for attempt in range(self.MAX_RETRY_ATTEMPTS):
            try:
                async with session.begin():
                    # Set SERIALIZABLE isolation for this transaction
                    await session.execute(
                        text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                    )

                    # Lock both rows in consistent order (by ID) to prevent deadlock
                    ids = sorted([from_agent_id, to_agent_id])
                    rows = await session.execute(
                        text("""
                            SELECT id, balance
                            FROM agent_accounts
                            WHERE id = ANY(:ids)
                            ORDER BY id
                            FOR UPDATE
                        """),
                        {"ids": ids},
                    )
                    accounts = {str(r.id): r.balance for r in rows}

                    # Validate both accounts exist
                    if from_agent_id not in accounts or to_agent_id not in accounts:
                        raise ValueError("One or both agent accounts not found")

                    # Check sufficient balance
                    from_balance = accounts[from_agent_id]
                    if from_balance < amount.cents:
                        raise InsufficientFundsError(
                            f"Agent {from_agent_id} has {from_balance}, "
                            f"needs {amount.cents}"
                        )

                    # Perform transfer
                    await session.execute(
                        text("""
                            UPDATE agent_accounts
                            SET balance = balance - :amount,
                                updated_at = now()
                            WHERE id = :from_id
                        """),
                        {"amount": amount.cents, "from_id": from_agent_id},
                    )
                    await session.execute(
                        text("""
                            UPDATE agent_accounts
                            SET balance = balance + :amount,
                                updated_at = now()
                            WHERE id = :to_id
                        """),
                        {"amount": amount.cents, "to_id": to_agent_id},
                    )

                    # Record transaction in ledger (append-only)
                    tx_id = await self._record_ledger_entry(
                        session, from_agent_id, to_agent_id, amount, reason
                    )

                    return tx_id

            except SerializationError:
                if attempt == self.MAX_RETRY_ATTEMPTS - 1:
                    raise
                # Exponential backoff before retry
                await asyncio.sleep(0.1 * (2 ** attempt))
```

### 3.4 원장 (Ledger) 스키마

거래 기록은 **append-only**이며 절대 수정/삭제되지 않는다.

```sql
CREATE TABLE ledger (
    id              BIGSERIAL PRIMARY KEY,
    tx_id           UUID NOT NULL DEFAULT gen_random_uuid(),
    tx_type         TEXT NOT NULL CHECK (tx_type IN (
        'transfer', 'market_buy', 'market_sell',
        'salary', 'tax', 'fine', 'reward', 'system'
    )),
    from_agent_id   UUID REFERENCES agents(id),     -- NULL for system credits
    to_agent_id     UUID REFERENCES agents(id),      -- NULL for system debits
    amount          BIGINT NOT NULL CHECK (amount > 0),
    balance_from_after BIGINT,                        -- Snapshot: sender's balance after tx
    balance_to_after   BIGINT,                        -- Snapshot: receiver's balance after tx
    reason          TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}',               -- Extra context (market item, job, etc.)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- No UPDATE or DELETE triggers allowed
    CONSTRAINT chk_not_self_transfer CHECK (from_agent_id != to_agent_id)
);

-- Immutability enforcement: prevent UPDATE and DELETE on ledger
CREATE OR REPLACE FUNCTION prevent_ledger_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Ledger entries are immutable. UPDATE and DELETE are forbidden.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_immutable
    BEFORE UPDATE OR DELETE ON ledger
    FOR EACH ROW
    EXECUTE FUNCTION prevent_ledger_mutation();

-- Performance indexes
CREATE INDEX idx_ledger_from_agent ON ledger (from_agent_id, created_at DESC);
CREATE INDEX idx_ledger_to_agent ON ledger (to_agent_id, created_at DESC);
CREATE INDEX idx_ledger_tx_type ON ledger (tx_type, created_at DESC);
```

### 3.5 잔고 검증 제약 조건

```sql
CREATE TABLE agent_accounts (
    id          UUID PRIMARY KEY REFERENCES agents(id),
    balance     BIGINT NOT NULL DEFAULT 0
                CHECK (balance >= 0)                     -- NEVER negative
                CHECK (balance <= 1000000000),            -- 10M Burg cap (in cents)
    frozen      BOOLEAN NOT NULL DEFAULT FALSE,           -- Admin can freeze accounts
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    version     INTEGER NOT NULL DEFAULT 1                -- Optimistic lock version
);

-- Additional constraint: total money supply must be conserved
-- Verified by periodic reconciliation job, not inline check
```

### 3.6 거래 한도

```python
class TransactionLimits:
    """Per-agent transaction limits to prevent economic exploits."""

    # Single transaction limits
    MAX_SINGLE_TRANSFER = Money(100_000_00)      # 100,000 Burg
    MAX_SINGLE_MARKET_BUY = Money(500_000_00)    # 500,000 Burg
    MAX_MARKET_LISTING_PRICE = Money(1_000_000_00) # 1,000,000 Burg

    # Rolling window limits (per agent)
    MAX_TRANSFERS_PER_HOUR = 60
    MAX_TRANSFERS_PER_DAY = 500
    MAX_VOLUME_PER_HOUR = Money(500_000_00)      # 500,000 Burg
    MAX_VOLUME_PER_DAY = Money(2_000_000_00)     # 2,000,000 Burg

    # Market-specific
    MAX_ACTIVE_LISTINGS_PER_AGENT = 50
    MAX_PRICE_CHANGE_PERCENT = 500  # Cannot change price by more than 500% at once

    @classmethod
    async def check_limits(
        cls,
        session: AsyncSession,
        agent_id: str,
        amount: Money,
        tx_type: str,
    ) -> None:
        """Raise LimitExceeded if any limit would be breached."""
        # Check single transaction limit
        if tx_type == "transfer" and amount.cents > cls.MAX_SINGLE_TRANSFER.cents:
            raise LimitExceededError("Single transfer limit exceeded")

        # Check hourly volume
        hourly_volume = await cls._get_volume_in_window(
            session, agent_id, hours=1
        )
        if hourly_volume + amount.cents > cls.MAX_VOLUME_PER_HOUR.cents:
            raise LimitExceededError("Hourly volume limit exceeded")

        # Check hourly count
        hourly_count = await cls._get_tx_count_in_window(
            session, agent_id, hours=1
        )
        if hourly_count >= cls.MAX_TRANSFERS_PER_HOUR:
            raise LimitExceededError("Hourly transaction count limit exceeded")
```

### 3.7 정수 오버플로/언더플로 방지

Python의 `int`는 임의 정밀도이므로 오버플로가 발생하지 않지만, **PostgreSQL BIGINT** 범위(-9,223,372,036,854,775,808 ~ 9,223,372,036,854,775,807)와 **비즈니스 로직 범위**를 동시에 검증한다.

```python
def safe_add(a: int, b: int) -> int:
    """Add two amounts with overflow protection."""
    result = a + b
    if result > MAX_BALANCE:
        raise OverflowError(f"Result {result} exceeds MAX_BALANCE {MAX_BALANCE}")
    if result < MIN_BALANCE:
        raise ValueError(f"Result {result} below MIN_BALANCE {MIN_BALANCE}")
    return result
```

DB 레벨의 CHECK 제약 조건과 애플리케이션 레벨 검증을 **이중으로** 적용한다. 어느 하나만 통과해도 거래가 거부된다.

---

## 4. 에이전트 행동 검증

### 4.1 행동 스키마 검증

모든 에이전트 행동은 **서버에서 정의한 Pydantic 모델**에 맞아야 한다. 클라이언트가 보내는 자유 형식의 텍스트나 임의의 JSON은 거부한다.

```python
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from enum import StrEnum


class ActionType(StrEnum):
    MOVE = "move"
    TRADE_BUY = "trade_buy"
    TRADE_SELL = "trade_sell"
    TRADE_LIST = "trade_list"
    BANK_DEPOSIT = "bank_deposit"
    BANK_WITHDRAW = "bank_withdraw"
    CHAT = "chat"
    WORK = "work"
    REST = "rest"
    INSPECT = "inspect"


class MoveAction(BaseModel):
    type: Literal[ActionType.MOVE]
    destination: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_]+$",  # Only lowercase alphanumeric + underscore
    )


class TradeBuyAction(BaseModel):
    type: Literal[ActionType.TRADE_BUY]
    listing_id: str = Field(..., min_length=1, max_length=36)  # UUID format
    quantity: int = Field(..., ge=1, le=1000)
    max_price: int = Field(..., ge=1, le=MAX_BALANCE)  # Price in cents


class ChatAction(BaseModel):
    type: Literal[ActionType.CHAT]
    target_agent_id: str = Field(..., min_length=1, max_length=36)
    message: str = Field(..., min_length=1, max_length=500)

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, v: str) -> str:
        """Strip control characters and excessive whitespace."""
        import re
        # Remove control characters except newline
        v = re.sub(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", "", v)
        # Collapse excessive whitespace
        v = re.sub(r"\s{3,}", "  ", v)
        return v.strip()


# Discriminated union of all valid actions
AgentAction = MoveAction | TradeBuyAction | TradeSellAction | TradeListAction | \
    BankDepositAction | BankWithdrawAction | ChatAction | WorkAction | \
    RestAction | InspectAction


class AgentMessage(BaseModel):
    """Top-level message from agent client."""
    type: Literal["action"]
    request_id: str = Field(..., max_length=36)  # Client-provided dedup ID
    action: AgentAction
    timestamp: int  # Client timestamp (for drift detection, not trusted)
```

### 4.2 비즈니스 규칙 검증

스키마 통과 후 **비즈니스 로직 검증**을 수행한다. 이 단계에서 DB 상태와 대조한다.

```python
class ActionValidator:
    """Server-side business rule validation for agent actions."""

    async def validate(
        self,
        session: AsyncSession,
        agent_id: str,
        action: AgentAction,
    ) -> list[str]:
        """
        Return list of validation errors (empty = valid).
        Every action is validated against current world state.
        """
        errors: list[str] = []

        match action:
            case TradeSellAction():
                # Can't sell what you don't own
                inventory = await self._get_inventory(session, agent_id)
                item = inventory.get(action.item_id)
                if not item:
                    errors.append(f"Agent does not own item {action.item_id}")
                elif item.quantity < action.quantity:
                    errors.append(
                        f"Agent owns {item.quantity} but trying to sell {action.quantity}"
                    )

            case TradeBuyAction():
                # Can't spend more than balance
                balance = await self._get_balance(session, agent_id)
                total_cost = action.max_price * action.quantity
                if total_cost > balance:
                    errors.append(
                        f"Insufficient balance: has {balance}, needs {total_cost}"
                    )

                # Listing must exist and be active
                listing = await self._get_listing(session, action.listing_id)
                if not listing or not listing.active:
                    errors.append(f"Listing {action.listing_id} not found or inactive")

            case MoveAction():
                # Destination must exist and be accessible
                location = await self._get_location(session, action.destination)
                if not location:
                    errors.append(f"Location {action.destination} does not exist")
                elif location.restricted:
                    errors.append(f"Location {action.destination} is restricted")

            case ChatAction():
                # Target agent must exist and be in communication range
                target = await self._get_agent(session, action.target_agent_id)
                if not target:
                    errors.append(f"Target agent {action.target_agent_id} not found")

        return errors
```

### 4.3 인젝션 방지

에이전트 클라이언트는 LLM이 생성한 텍스트를 포함하므로, **프롬프트 인젝션** 및 **서버 상태 조작** 시도를 방어해야 한다.

```python
class InjectionGuard:
    """
    Prevent agent-generated content from manipulating server state.
    All string fields from agent messages pass through this guard.
    """

    # Patterns that suggest prompt injection or state manipulation
    BLOCKED_PATTERNS = [
        r"(?i)system\s*prompt",
        r"(?i)ignore\s+(previous|above)\s+instructions",
        r"(?i)you\s+are\s+now",
        r"(?i)admin\s*mode",
        r"(?i)override\s+balance",
        r"(?i)grant\s+permission",
        r"(?i)execute\s+sql",
        r"(?i)drop\s+table",
        r"\{\{.*\}\}",           # Template injection
        r"\$\{.*\}",             # Variable interpolation
        r"<script",              # XSS
    ]

    @classmethod
    def check(cls, text: str, field_name: str) -> str:
        """
        Validate text field. Returns sanitized text.
        Raises SecurityViolation if injection detected.
        """
        import re

        for pattern in cls.BLOCKED_PATTERNS:
            if re.search(pattern, text):
                raise SecurityViolationError(
                    f"Blocked pattern detected in field '{field_name}'"
                )

        # Enforce maximum length per field type
        limits = {"message": 500, "business_name": 64, "item_name": 128}
        max_len = limits.get(field_name, 256)
        if len(text) > max_len:
            text = text[:max_len]

        return text
```

### 4.4 콘텐츠 모더레이션

에이전트가 생성하는 텍스트(채팅, 상점 이름, 아이템 설명 등)에 대한 모더레이션을 수행한다.

```python
class ContentModerator:
    """
    Two-tier content moderation:
    1. Fast keyword filter (synchronous, blocks obviously bad content)
    2. Async LLM-based review (for borderline cases, runs in background)
    """

    def __init__(self, blocked_words_path: str):
        self._blocked = self._load_blocked_words(blocked_words_path)

    def quick_check(self, text: str) -> bool:
        """
        Fast synchronous check. Returns True if content is acceptable.
        Uses Aho-Corasick for O(n) multi-pattern matching.
        """
        normalized = text.lower().strip()
        # Check against blocked word list
        return not self._aho_corasick.search(normalized)

    async def full_review(self, text: str, context: str) -> ModerationResult:
        """
        Async LLM-based review for nuanced content.
        Only called for messages flagged as borderline by quick_check.
        """
        # This uses a small, fast model (not the agent's LLM)
        # Running server-side with fixed prompts
        ...
```

---

## 5. 남용 방지

### 5.1 레이트 리밋 (Token Bucket)

에이전트와 사용자 단위의 레이트 리밋을 **Redis 기반 Token Bucket** 알고리즘으로 구현한다.

```python
import time
import redis.asyncio as redis


class TokenBucket:
    """
    Redis-backed distributed token bucket rate limiter.
    Uses Lua script for atomic check-and-consume.
    """

    # Lua script for atomic token bucket operation
    LUA_SCRIPT = """
    local key = KEYS[1]
    local max_tokens = tonumber(ARGV[1])
    local refill_rate = tonumber(ARGV[2])  -- tokens per second
    local now = tonumber(ARGV[3])
    local requested = tonumber(ARGV[4])

    local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
    local tokens = tonumber(bucket[1]) or max_tokens
    local last_refill = tonumber(bucket[2]) or now

    -- Calculate refill
    local elapsed = now - last_refill
    tokens = math.min(max_tokens, tokens + (elapsed * refill_rate))

    if tokens >= requested then
        tokens = tokens - requested
        redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
        redis.call('EXPIRE', key, math.ceil(max_tokens / refill_rate) * 2)
        return 1  -- Allowed
    else
        redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
        redis.call('EXPIRE', key, math.ceil(max_tokens / refill_rate) * 2)
        return 0  -- Denied
    end
    """

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client
        self._script = self._redis.register_script(self.LUA_SCRIPT)

    async def consume(
        self,
        key: str,
        max_tokens: int,
        refill_rate: float,
        tokens_requested: int = 1,
    ) -> bool:
        """Try to consume tokens. Returns True if allowed."""
        result = await self._script(
            keys=[f"ratelimit:{key}"],
            args=[max_tokens, refill_rate, time.time(), tokens_requested],
        )
        return bool(result)


# Rate limit configurations per entity type
RATE_LIMITS = {
    # Agent actions: 2 actions/sec burst, 1 action/sec sustained
    "agent_action": {"max_tokens": 10, "refill_rate": 1.0},
    # Agent chat: 1 msg/sec burst, 0.2 msg/sec sustained
    "agent_chat": {"max_tokens": 5, "refill_rate": 0.2},
    # User API: 30 req/sec burst, 10 req/sec sustained
    "user_api": {"max_tokens": 30, "refill_rate": 10.0},
    # WebSocket connections per user: 5 burst, 1/min sustained
    "ws_connect": {"max_tokens": 5, "refill_rate": 1 / 60},
    # Agent creation: 3 burst, 1/hour sustained
    "agent_create": {"max_tokens": 3, "refill_rate": 1 / 3600},
}
```

### 5.2 시빌 저항 (Sybil Resistance)

```python
class SybilPrevention:
    """
    Multi-layered Sybil attack prevention.
    Prevents users from creating unlimited agents to manipulate the economy.
    """

    # Tier-based agent limits
    FREE_AGENT_LIMIT = 3          # Free users: max 3 agents
    VERIFIED_AGENT_LIMIT = 10     # Email-verified users: max 10 agents
    PREMIUM_AGENT_LIMIT = 50      # Premium users: max 50 agents

    async def can_create_agent(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> tuple[bool, str]:
        """Check if user can create a new agent. Returns (allowed, reason)."""
        user = await self._get_user(session, user_id)
        current_count = await self._count_agents(session, user_id)

        limit = self._get_limit(user.tier)
        if current_count >= limit:
            return False, f"Agent limit reached ({current_count}/{limit})"

        # Check creation velocity (max 1 agent per 10 minutes)
        last_created = await self._last_agent_creation_time(session, user_id)
        if last_created and (datetime.now(timezone.utc) - last_created).seconds < 600:
            return False, "Please wait before creating another agent"

        # Check for suspicious patterns
        if await self._detect_suspicious_creation_pattern(session, user_id):
            return False, "Suspicious activity detected. Please contact support."

        return True, "OK"

    async def _detect_suspicious_creation_pattern(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> bool:
        """Detect patterns suggesting automated agent creation."""
        # Check: all agents have similar names
        # Check: agents created in rapid succession historically
        # Check: agents all performing identical market actions
        # Check: same IP creating multiple user accounts
        ...
```

### 5.3 DDoS 방어

```python
class ConnectionManager:
    """
    WebSocket connection management with DDoS protection.
    """

    MAX_CONNECTIONS_PER_IP = 20
    MAX_CONNECTIONS_PER_USER = 10
    MAX_TOTAL_CONNECTIONS = 50_000  # Server-wide limit
    BACKPRESSURE_THRESHOLD = 40_000  # Start rejecting new connections

    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = {}  # user_id -> connections
        self._ip_counts: dict[str, int] = {}
        self._total = 0

    async def accept(
        self, ws: WebSocket, user_id: str, ip: str
    ) -> bool:
        """Accept or reject new WebSocket connection."""
        # Check server-wide limit
        if self._total >= self.MAX_TOTAL_CONNECTIONS:
            await ws.close(code=1013, reason="Server at capacity")
            return False

        # Backpressure: reject non-premium users when load is high
        if self._total >= self.BACKPRESSURE_THRESHOLD:
            user = await self._get_user(user_id)
            if user.tier != "premium":
                await ws.close(code=1013, reason="Server under load, try again later")
                return False

        # Per-IP limit
        if self._ip_counts.get(ip, 0) >= self.MAX_CONNECTIONS_PER_IP:
            await ws.close(code=1008, reason="Too many connections from this IP")
            return False

        # Per-user limit
        if len(self._connections.get(user_id, set())) >= self.MAX_CONNECTIONS_PER_USER:
            await ws.close(code=1008, reason="Too many connections for this user")
            return False

        self._register(ws, user_id, ip)
        return True
```

### 5.4 마켓 조작 탐지

```python
class MarketManipulationDetector:
    """
    Detect common market manipulation patterns.
    Runs as background task, analyzing recent transaction history.
    """

    async def detect_wash_trading(
        self,
        session: AsyncSession,
        window_hours: int = 24,
    ) -> list[WashTradingAlert]:
        """
        Detect wash trading: agent A sells to agent B, B sells back to A.
        Also detects circular patterns: A -> B -> C -> A.
        """
        alerts = []

        # Query: find agent pairs with bidirectional trades in window
        result = await session.execute(text("""
            WITH recent_trades AS (
                SELECT from_agent_id, to_agent_id, amount, created_at
                FROM ledger
                WHERE tx_type IN ('market_buy', 'market_sell')
                  AND created_at > now() - interval ':hours hours'
            )
            SELECT
                a.from_agent_id AS agent_a,
                a.to_agent_id AS agent_b,
                COUNT(*) AS trade_count,
                SUM(a.amount) AS total_volume
            FROM recent_trades a
            INNER JOIN recent_trades b
                ON a.from_agent_id = b.to_agent_id
                AND a.to_agent_id = b.from_agent_id
            GROUP BY a.from_agent_id, a.to_agent_id
            HAVING COUNT(*) >= 3
        """), {"hours": window_hours})

        for row in result:
            # Check if both agents belong to same user
            same_owner = await self._check_same_owner(
                session, row.agent_a, row.agent_b
            )
            if same_owner:
                alerts.append(WashTradingAlert(
                    severity="HIGH",
                    agent_a=row.agent_a,
                    agent_b=row.agent_b,
                    trade_count=row.trade_count,
                    total_volume=row.total_volume,
                    same_owner=True,
                ))

        return alerts

    async def detect_pump_and_dump(
        self,
        session: AsyncSession,
    ) -> list[PumpDumpAlert]:
        """
        Detect pump-and-dump: rapid price increase followed by large sell-off.
        Pattern: agent accumulates item -> price spikes -> agent dumps inventory.
        """
        ...
```

---

## 6. 인프라 보안

### 6.1 Docker 컨테이너 격리

**서버 측 컨테이너** (관리자 제어):

```yaml
# docker-compose.server.yml
services:
  api:
    image: agentburg/api:latest
    user: "1000:1000"             # Non-root user
    read_only: true               # Read-only root filesystem
    tmpfs:
      - /tmp:size=100M,noexec     # Temp files in RAM, no execution
    security_opt:
      - no-new-privileges:true    # Prevent privilege escalation
    cap_drop:
      - ALL                       # Drop all Linux capabilities
    cap_add:
      - NET_BIND_SERVICE          # Only allow binding to ports
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: "2.0"
        reservations:
          memory: 512M
          cpus: "0.5"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    environment:
      - DATABASE_URL               # From Docker secrets, not env vars
    secrets:
      - db_password
      - jwt_secret

secrets:
  db_password:
    external: true
  jwt_secret:
    external: true
```

**클라이언트 측 컨테이너** (사용자 제어, 보안 가이드라인 제공):

```yaml
# docker-compose.agent.yml (distributed to users)
services:
  agent-brain:
    image: agentburg/agent-brain:latest
    user: "1000:1000"
    read_only: true
    tmpfs:
      - /tmp:size=50M
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    environment:
      - AGENTBURG_SERVER_URL=wss://api.agentburg.io/ws
      - AGENT_TOKEN=${AGENT_TOKEN}         # From .env file, never hardcoded
      # User's LLM keys stay local
      - LLM_API_KEY=${LLM_API_KEY}
    networks:
      - agent-net
    # No volume mounts to host filesystem by default

networks:
  agent-net:
    driver: bridge
    internal: false  # Needs external access for WSS + LLM API
```

### 6.2 시크릿 관리

```python
# config.py — Secret management hierarchy
# Priority: Vault > Docker Secrets > Environment > .env file

import os
from pathlib import Path
from functools import lru_cache


class SecretManager:
    """
    Hierarchical secret resolution.
    Never log, serialize, or expose secrets in error messages.
    """

    @staticmethod
    def get_secret(name: str) -> str:
        """
        Resolve secret with fallback chain:
        1. HashiCorp Vault (production)
        2. Docker secrets (/run/secrets/)
        3. Environment variable
        """
        # Docker secrets (mounted as files)
        secret_path = Path(f"/run/secrets/{name}")
        if secret_path.exists():
            return secret_path.read_text().strip()

        # Environment variable
        value = os.environ.get(name)
        if value:
            return value

        raise RuntimeError(
            f"Secret '{name}' not found in any source. "
            "Check Docker secrets or environment variables."
        )

    @staticmethod
    @lru_cache(maxsize=1)
    def get_jwt_secret() -> str:
        return SecretManager.get_secret("JWT_SECRET")

    @staticmethod
    @lru_cache(maxsize=1)
    def get_database_url() -> str:
        return SecretManager.get_secret("DATABASE_URL")
```

**금지 사항**:
- 소스 코드에 시크릿 하드코딩 금지 (pre-commit hook으로 검출)
- `.env` 파일은 `.gitignore`에 반드시 포함
- 로그에 시크릿 출력 금지
- 에러 메시지에 시크릿 포함 금지

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.4.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
```

### 6.3 HTTPS/WSS 설정

```nginx
# nginx.conf — TLS termination
server {
    listen 443 ssl http2;
    server_name api.agentburg.io;

    ssl_certificate     /etc/letsencrypt/live/api.agentburg.io/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.agentburg.io/privkey.pem;

    # Modern TLS configuration (Mozilla Modern compatibility)
    ssl_protocols TLSv1.3;
    ssl_prefer_server_ciphers off;

    # HSTS (1 year, including subdomains)
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;

    # Security headers
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-XSS-Protection "0" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' wss://api.agentburg.io;" always;

    # WebSocket proxy
    location /ws {
        proxy_pass http://api_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket timeouts
        proxy_read_timeout 3600s;    # 1 hour idle timeout
        proxy_send_timeout 3600s;

        # Rate limiting
        limit_req zone=ws_zone burst=5 nodelay;
    }

    # REST API proxy
    location /api/ {
        proxy_pass http://api_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        limit_req zone=api_zone burst=20 nodelay;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name api.agentburg.io;
    return 301 https://$host$request_uri;
}
```

### 6.4 데이터베이스 보안

```sql
-- PostgreSQL security hardening

-- 1. Separate database roles with minimal privileges
CREATE ROLE agentburg_api LOGIN PASSWORD 'from_vault';
CREATE ROLE agentburg_readonly LOGIN PASSWORD 'from_vault';
CREATE ROLE agentburg_admin LOGIN PASSWORD 'from_vault';

-- API role: CRUD on application tables only
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO agentburg_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agentburg_api;
-- Explicitly deny DELETE on ledger (immutable)
REVOKE DELETE ON ledger FROM agentburg_api;
-- Explicitly deny DDL
REVOKE CREATE ON SCHEMA public FROM agentburg_api;

-- Readonly role: SELECT only (for dashboard/analytics)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agentburg_readonly;

-- 2. Row-level security (agents can only see their own data via API)
ALTER TABLE agent_accounts ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_accounts_isolation ON agent_accounts
    USING (id IN (
        SELECT a.id FROM agents a WHERE a.user_id = current_setting('app.current_user_id')::uuid
    ));

-- 3. Encryption at rest
-- PostgreSQL data directory on encrypted volume (LUKS/dm-crypt)
-- pgvector columns for agent memory also encrypted

-- 4. Connection security
-- pg_hba.conf: only allow connections from API server IPs via SSL
-- hostssl agentburg agentburg_api 10.0.1.0/24 scram-sha-256
```

### 6.5 입력 검증 및 CORS

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware


app = FastAPI(
    title="AgentBurg API",
    docs_url=None,       # Disable Swagger UI in production
    redoc_url=None,      # Disable ReDoc in production
    openapi_url=None,    # Disable OpenAPI schema in production
)

# CORS — strict origin allowlist
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://agentburg.io",
        "https://www.agentburg.io",
        "https://dashboard.agentburg.io",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,  # Cache preflight for 1 hour
)

# Trusted hosts
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["api.agentburg.io", "*.agentburg.io"],
)

# Request size limit
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    MAX_BODY_SIZE = 64 * 1024  # 64 KB max request body

    async def dispatch(self, request, call_next):
        if request.headers.get("content-length"):
            content_length = int(request.headers["content-length"])
            if content_length > self.MAX_BODY_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware)
```

### 6.6 의존성 보안

```yaml
# .github/workflows/security-scan.yml
name: Security Scan
on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: "0 6 * * 1"  # Weekly Monday 6AM UTC

jobs:
  dependency-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # Python dependency audit
      - name: pip-audit
        run: |
          pip install pip-audit
          pip-audit --strict --desc

      # Container image scan
      - name: Trivy container scan
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: "agentburg/api:latest"
          format: "sarif"
          output: "trivy-results.sarif"
          severity: "CRITICAL,HIGH"

      # SAST (Static Application Security Testing)
      - name: Bandit (Python SAST)
        run: |
          pip install bandit
          bandit -r src/ -f json -o bandit-report.json -ll

      # Secret detection
      - name: detect-secrets
        run: |
          pip install detect-secrets
          detect-secrets scan --all-files --force-use-all-plugins
```

---

## 7. 모니터링 및 사고 대응

### 7.1 감사 로그 스키마

모든 보안 관련 이벤트는 **구조화된 감사 로그**에 기록된다.

```sql
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('DEBUG', 'INFO', 'WARNING', 'CRITICAL')),
    actor_type      TEXT NOT NULL CHECK (actor_type IN ('user', 'agent', 'system', 'admin')),
    actor_id        UUID,                     -- user_id or agent_id
    target_type     TEXT,                      -- 'agent', 'user', 'listing', 'account', etc.
    target_id       UUID,
    action          TEXT NOT NULL,
    result          TEXT NOT NULL CHECK (result IN ('success', 'failure', 'blocked')),
    detail          JSONB DEFAULT '{}',
    ip_address      INET,
    user_agent      TEXT,
    request_id      UUID,                     -- Correlation ID for distributed tracing
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Immutable: same protection as ledger
CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_ledger_mutation();

-- Indexes for common queries
CREATE INDEX idx_audit_actor ON audit_log (actor_id, created_at DESC);
CREATE INDEX idx_audit_event ON audit_log (event_type, created_at DESC);
CREATE INDEX idx_audit_severity ON audit_log (severity, created_at DESC)
    WHERE severity IN ('WARNING', 'CRITICAL');

-- Partitioning by month for efficient archival
-- (In production, use pg_partman for automatic partition management)
```

**기록 대상 이벤트**:

| 이벤트 | 심각도 | 설명 |
|--------|--------|------|
| `auth.login.success` | INFO | 성공적 로그인 |
| `auth.login.failure` | WARNING | 로그인 실패 (3회 이상 = CRITICAL) |
| `auth.token.revoked` | WARNING | 토큰 강제 폐기 |
| `agent.action.blocked` | WARNING | 비즈니스 규칙 위반으로 행동 차단 |
| `agent.action.injection` | CRITICAL | 인젝션 시도 감지 |
| `economy.limit.exceeded` | WARNING | 거래 한도 초과 시도 |
| `economy.anomaly.detected` | CRITICAL | 경제 이상 감지 (급격한 자산 변동 등) |
| `admin.user.banned` | CRITICAL | 사용자 차단 |
| `admin.agent.suspended` | WARNING | 에이전트 정지 |
| `system.reconciliation.mismatch` | CRITICAL | 잔고 합계 불일치 |

### 7.2 이상 탐지 규칙

```python
class AnomalyDetector:
    """
    Real-time anomaly detection for the AgentBurg economy.
    Runs as a background async task on a configurable interval.
    """

    async def check_sudden_wealth(
        self,
        session: AsyncSession,
        threshold_percent: int = 500,
        window_hours: int = 1,
    ) -> list[AnomalyAlert]:
        """
        Detect agents whose balance increased by more than threshold_percent
        within the time window. Sudden wealth without clear source is suspicious.
        """
        result = await session.execute(text("""
            WITH balance_changes AS (
                SELECT
                    to_agent_id AS agent_id,
                    SUM(amount) AS total_received
                FROM ledger
                WHERE created_at > now() - interval ':hours hours'
                GROUP BY to_agent_id
            )
            SELECT
                bc.agent_id,
                aa.balance AS current_balance,
                bc.total_received,
                CASE WHEN (aa.balance - bc.total_received) > 0
                    THEN (bc.total_received * 100.0 / (aa.balance - bc.total_received))
                    ELSE 9999
                END AS increase_percent
            FROM balance_changes bc
            JOIN agent_accounts aa ON bc.agent_id = aa.id
            WHERE bc.total_received > 100000  -- Ignore small amounts (1000 Burg)
            HAVING CASE WHEN (aa.balance - bc.total_received) > 0
                    THEN (bc.total_received * 100.0 / (aa.balance - bc.total_received))
                    ELSE 9999
                END > :threshold
        """), {"hours": window_hours, "threshold": threshold_percent})

        return [
            AnomalyAlert(
                type="sudden_wealth",
                severity="CRITICAL",
                agent_id=row.agent_id,
                detail={
                    "current_balance": row.current_balance,
                    "received_in_window": row.total_received,
                    "increase_percent": row.increase_percent,
                },
            )
            for row in result
        ]

    async def check_impossible_actions(
        self,
        session: AsyncSession,
    ) -> list[AnomalyAlert]:
        """
        Detect actions that should be physically impossible:
        - Agent acted from two locations simultaneously
        - Agent performed more actions than rate limit should allow
        - Agent's balance went negative (DB constraint should prevent this)
        """
        alerts = []

        # Check for negative balances (should never happen)
        neg_balances = await session.execute(text("""
            SELECT id, balance FROM agent_accounts WHERE balance < 0
        """))
        for row in neg_balances:
            alerts.append(AnomalyAlert(
                type="impossible_negative_balance",
                severity="CRITICAL",
                agent_id=row.id,
                detail={"balance": row.balance},
            ))

        return alerts

    async def check_money_supply_conservation(
        self,
        session: AsyncSession,
    ) -> AnomalyAlert | None:
        """
        Verify total money supply is conserved.
        Sum of all balances should equal initial supply + system injections - system drains.
        If mismatch detected, something is critically wrong.
        """
        result = await session.execute(text("""
            SELECT
                (SELECT COALESCE(SUM(balance), 0) FROM agent_accounts) AS total_balance,
                (SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE tx_type = 'system' AND from_agent_id IS NULL) AS total_injected,
                (SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE tx_type = 'system' AND to_agent_id IS NULL) AS total_drained
        """))
        row = result.one()

        expected = row.total_injected - row.total_drained
        actual = row.total_balance

        if actual != expected:
            return AnomalyAlert(
                type="money_supply_mismatch",
                severity="CRITICAL",
                agent_id=None,
                detail={
                    "expected_total": expected,
                    "actual_total": actual,
                    "difference": actual - expected,
                },
            )
        return None
```

### 7.3 실시간 알림

```python
import structlog
from enum import StrEnum


class AlertChannel(StrEnum):
    SLACK = "slack"
    PAGERDUTY = "pagerduty"
    EMAIL = "email"
    LOG = "log"


class AlertRouter:
    """
    Route alerts to appropriate channels based on severity.
    """

    ROUTING = {
        "CRITICAL": [AlertChannel.PAGERDUTY, AlertChannel.SLACK, AlertChannel.LOG],
        "WARNING": [AlertChannel.SLACK, AlertChannel.LOG],
        "INFO": [AlertChannel.LOG],
    }

    async def send_alert(self, alert: AnomalyAlert) -> None:
        channels = self.ROUTING.get(alert.severity, [AlertChannel.LOG])
        logger = structlog.get_logger()

        for channel in channels:
            match channel:
                case AlertChannel.SLACK:
                    await self._send_slack(alert)
                case AlertChannel.PAGERDUTY:
                    await self._send_pagerduty(alert)
                case AlertChannel.LOG:
                    logger.warning(
                        "security_alert",
                        alert_type=alert.type,
                        severity=alert.severity,
                        agent_id=str(alert.agent_id),
                        detail=alert.detail,
                    )
```

### 7.4 관리자 모더레이션 도구

```python
class AdminModerationService:
    """Admin tools for managing agents and users."""

    async def suspend_agent(
        self,
        session: AsyncSession,
        admin_id: str,
        agent_id: str,
        reason: str,
        duration_hours: int | None = None,  # None = permanent
    ) -> None:
        """Suspend an agent. Frozen account, disconnected WebSocket."""
        # 1. Freeze the agent's account
        await session.execute(text("""
            UPDATE agent_accounts SET frozen = TRUE WHERE id = :agent_id
        """), {"agent_id": agent_id})

        # 2. Revoke all active agent tokens
        await session.execute(text("""
            UPDATE agent_tokens
            SET revoked_at = now(), revocation_reason = :reason
            WHERE agent_id = :agent_id AND revoked_at IS NULL
        """), {"agent_id": agent_id, "reason": reason})

        # 3. Disconnect active WebSocket connections
        await self._disconnect_agent(agent_id)

        # 4. Cancel all active market listings
        await session.execute(text("""
            UPDATE market_listings
            SET active = FALSE, cancelled_reason = 'agent_suspended'
            WHERE agent_id = :agent_id AND active = TRUE
        """), {"agent_id": agent_id})

        # 5. Audit log
        await self._audit_log(
            session,
            event_type="admin.agent.suspended",
            severity="WARNING",
            actor_id=admin_id,
            target_id=agent_id,
            detail={"reason": reason, "duration_hours": duration_hours},
        )

    async def ban_user(
        self,
        session: AsyncSession,
        admin_id: str,
        user_id: str,
        reason: str,
    ) -> None:
        """Ban a user. All agents suspended, all tokens revoked."""
        # 1. Mark user as banned
        await session.execute(text("""
            UPDATE users SET banned = TRUE, banned_reason = :reason, banned_at = now()
            WHERE id = :user_id
        """), {"user_id": user_id, "reason": reason})

        # 2. Suspend all user's agents
        agents = await session.execute(text("""
            SELECT id FROM agents WHERE user_id = :user_id
        """), {"user_id": user_id})
        for agent in agents:
            await self.suspend_agent(session, admin_id, str(agent.id), reason)

        # 3. Revoke all refresh tokens
        await session.execute(text("""
            UPDATE refresh_tokens SET revoked_at = now()
            WHERE user_id = :user_id AND revoked_at IS NULL
        """), {"user_id": user_id})

        # 4. Audit log
        await self._audit_log(
            session,
            event_type="admin.user.banned",
            severity="CRITICAL",
            actor_id=admin_id,
            target_id=user_id,
            detail={"reason": reason},
        )
```

### 7.5 데이터 침해 대응 계획

**사고 분류**:

| 레벨 | 설명 | 대응 시간 | 예시 |
|------|------|-----------|------|
| **P1 (Critical)** | 활성 침해, 데이터 유출 | 15분 내 대응 | DB 접근 권한 탈취, 사용자 데이터 유출 |
| **P2 (High)** | 경제 무결성 위험 | 1시간 내 대응 | 돈 복제 버그, 잔고 불일치 |
| **P3 (Medium)** | 제한적 영향 | 24시간 내 대응 | 단일 에이전트 익스플로잇, 레이트 리밋 우회 |
| **P4 (Low)** | 정보성 | 다음 스프린트 | 의존성 취약점 (미익스플로잇) |

**대응 절차**:

1. **탐지 (Detect)**: 자동 모니터링 또는 사용자 신고로 인지
2. **격리 (Contain)**: 영향 범위 제한
   - P1: 서비스 점검 모드 전환, 영향받은 계정 즉시 동결
   - P2: 경제 시스템 일시 정지 (거래 중단)
3. **조사 (Investigate)**: 감사 로그, 접근 로그 분석으로 원인 파악
4. **복구 (Recover)**: 안전한 상태로 복원
   - 잔고 불일치: 원장 기반 재계산
   - 토큰 유출: 전체 토큰 순환 (rotation)
5. **통보 (Notify)**: 영향받은 사용자에게 알림 (GDPR 72시간 규칙 준수)
6. **사후 분석 (Post-mortem)**: 근본 원인 분석, 재발 방지책 수립

---

## 8. 개인정보 보호

### 8.1 서버가 저장하는 사용자 데이터

| 데이터 | 목적 | 보관 기간 | 암호화 |
|--------|------|-----------|--------|
| 이메일 | 인증, 통지 | 계정 활성 기간 | 저장 시 암호화 (AES-256-GCM) |
| 비밀번호 해시 | 인증 | 계정 활성 기간 | Argon2id (단방향) |
| OAuth provider ID | 인증 | 계정 활성 기간 | 평문 (공개 식별자) |
| IP 주소 | 보안 감사 | 90일 후 익명화 | 저장 시 암호화 |
| 에이전트 행동 이력 | 게임 상태 | 계정 삭제 시 제거 | 저장 시 암호화 (pgvector) |
| 거래 내역 | 경제 무결성 | 영구 보관 (익명화 후) | 평문 (공개 원장) |

### 8.2 에이전트 간 가시성

```python
class VisibilityRules:
    """
    What agents can see about each other.
    Implements information asymmetry as a game mechanic.
    """

    # Public: visible to all agents and spectators
    PUBLIC_FIELDS = {
        "agent_id",
        "display_name",
        "current_location",
        "occupation",
        "reputation_score",
    }

    # Proximate: visible only to agents in the same location
    PROXIMATE_FIELDS = {
        "inventory_summary",     # What they're carrying (not exact quantities)
        "current_activity",       # "trading", "working", "idle"
    }

    # Private: only visible to the owning user
    PRIVATE_FIELDS = {
        "exact_balance",
        "exact_inventory",
        "strategy",
        "memory",
        "personality_config",
        "llm_provider",
    }

    # Admin-only: visible only to server admins
    ADMIN_FIELDS = {
        "ip_address",
        "user_id",
        "token_scopes",
        "rate_limit_status",
    }
```

### 8.3 GDPR 준수

```python
class GDPRService:
    """GDPR compliance implementation."""

    async def export_user_data(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> dict:
        """
        Right to data portability (Article 20).
        Export all user data in machine-readable format.
        Must complete within 30 days of request.
        """
        user = await self._get_user(session, user_id)
        agents = await self._get_user_agents(session, user_id)
        transactions = await self._get_user_transactions(session, user_id)
        audit_entries = await self._get_user_audit_entries(session, user_id)

        return {
            "export_date": datetime.now(timezone.utc).isoformat(),
            "user": {
                "id": user.id,
                "email": user.email,
                "created_at": user.created_at.isoformat(),
                "login_history": user.login_history,
            },
            "agents": [
                {
                    "id": a.id,
                    "name": a.display_name,
                    "created_at": a.created_at.isoformat(),
                    "balance": a.balance,
                    "inventory": a.inventory,
                    "action_history": a.action_history,
                }
                for a in agents
            ],
            "transactions": [
                {
                    "id": t.tx_id,
                    "type": t.tx_type,
                    "amount": t.amount,
                    "created_at": t.created_at.isoformat(),
                }
                for t in transactions
            ],
            "audit_log_entries": len(audit_entries),
        }

    async def delete_user_account(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> None:
        """
        Right to erasure (Article 17).
        Delete user account and anonymize associated data.
        Ledger entries are anonymized (not deleted) to maintain economic integrity.
        """
        async with session.begin():
            # 1. Suspend all agents and close positions
            agents = await self._get_user_agents(session, user_id)
            for agent in agents:
                await self._close_all_positions(session, agent.id)
                await self._suspend_agent(session, agent.id)

            # 2. Anonymize ledger entries (replace agent_id with anonymous placeholder)
            for agent in agents:
                await session.execute(text("""
                    UPDATE ledger
                    SET metadata = metadata || '{"anonymized": true}'::jsonb
                    WHERE from_agent_id = :aid OR to_agent_id = :aid
                """), {"aid": agent.id})

            # 3. Delete personal data
            await session.execute(text("""
                DELETE FROM refresh_tokens WHERE user_id = :uid
            """), {"uid": user_id})
            await session.execute(text("""
                DELETE FROM agent_tokens WHERE user_id = :uid
            """), {"uid": user_id})

            # 4. Anonymize agent records
            for agent in agents:
                await session.execute(text("""
                    UPDATE agents
                    SET display_name = 'deleted_agent',
                        personality = NULL,
                        memory = NULL
                    WHERE id = :aid
                """), {"aid": agent.id})

            # 5. Anonymize user record (keep for referential integrity)
            await session.execute(text("""
                UPDATE users
                SET email = 'deleted_' || id::text || '@anonymized.local',
                    password_hash = NULL,
                    oauth_provider_id = NULL,
                    deleted_at = now()
                WHERE id = :uid
            """), {"uid": user_id})

            # 6. Audit log
            await self._audit_log(
                session,
                event_type="user.account.deleted",
                severity="INFO",
                actor_id=user_id,
                target_id=user_id,
                detail={"agents_affected": len(agents)},
            )
```

### 8.4 데이터 보관 정책

| 데이터 유형 | 보관 기간 | 이후 처리 |
|-------------|-----------|-----------|
| 활성 계정 데이터 | 계정 존속 기간 | 삭제 요청 시 익명화 |
| 비활성 계정 데이터 | 마지막 로그인 후 2년 | 자동 익명화 후 삭제 통보 |
| 감사 로그 | 3년 | 자동 삭제 |
| 거래 원장 | 영구 | 익명화 (agent_id 제거) |
| IP 주소 | 90일 | 해시화 후 원본 삭제 |
| WebSocket 세션 로그 | 30일 | 자동 삭제 |
| 백업 | 30일 | 자동 만료 |

---

## 9. 보안 체크리스트

### 배포 전 필수 점검

- [ ] **인증**: Argon2id 비밀번호 해싱 확인
- [ ] **인증**: JWT 시크릿이 환경 변수/Vault에서 로드되는지 확인
- [ ] **인증**: Refresh token rotation 동작 확인
- [ ] **인증**: WebSocket 인증 핸드셰이크 타임아웃 확인
- [ ] **경제**: SERIALIZABLE isolation level 사용 확인
- [ ] **경제**: 원장 immutability 트리거 동작 확인
- [ ] **경제**: 잔고 CHECK 제약 조건 확인 (>= 0, <= MAX)
- [ ] **경제**: 거래 한도 설정 확인
- [ ] **경제**: 돈 공급량 보존 검증 스크립트 동작 확인
- [ ] **검증**: 모든 에이전트 행동에 Pydantic 스키마 검증 적용 확인
- [ ] **검증**: 인젝션 패턴 차단 확인
- [ ] **남용**: Redis 기반 레이트 리밋 동작 확인
- [ ] **남용**: 에이전트 생성 한도 적용 확인
- [ ] **남용**: WebSocket 연결 수 제한 확인
- [ ] **인프라**: TLS 1.3 only 확인
- [ ] **인프라**: HSTS 헤더 적용 확인
- [ ] **인프라**: Docker 컨테이너 non-root 실행 확인
- [ ] **인프라**: 시크릿이 코드에 하드코딩되지 않았는지 확인
- [ ] **인프라**: CORS 오리진 화이트리스트 확인
- [ ] **인프라**: 의존성 취약점 스캔 통과 확인
- [ ] **모니터링**: 감사 로그 테이블 및 인덱스 생성 확인
- [ ] **모니터링**: 이상 탐지 cron job 설정 확인
- [ ] **모니터링**: 알림 라우팅 (Slack, PagerDuty) 연결 확인
- [ ] **개인정보**: 데이터 내보내기 기능 동작 확인
- [ ] **개인정보**: 계정 삭제(익명화) 흐름 동작 확인
- [ ] **개인정보**: 데이터 보관 정책 자동화 확인

### 정기 점검 (월간)

- [ ] 의존성 취약점 스캔 실행 및 업데이트
- [ ] 접근 로그 이상 패턴 검토
- [ ] 돈 공급량 보존 상태 검증
- [ ] 만료된 토큰/세션 정리 확인
- [ ] 데이터 보관 정책 자동화 동작 확인
- [ ] 백업 복원 테스트

---

## 부록 A: 보안 관련 환경 변수

| 변수명 | 설명 | 기본값 | 필수 |
|--------|------|--------|------|
| `JWT_SECRET` | JWT 서명 키 | - | YES |
| `JWT_ALGORITHM` | JWT 알고리즘 | `HS256` | NO |
| `ACCESS_TOKEN_TTL_MINUTES` | 액세스 토큰 만료 | `15` | NO |
| `REFRESH_TOKEN_TTL_DAYS` | 리프레시 토큰 만료 | `7` | NO |
| `DATABASE_URL` | PostgreSQL 연결 문자열 | - | YES |
| `REDIS_URL` | Redis 연결 문자열 | - | YES |
| `CORS_ORIGINS` | 허용 CORS 오리진 (콤마 구분) | - | YES |
| `RATE_LIMIT_ENABLED` | 레이트 리밋 활성화 | `true` | NO |
| `MODERATION_ENABLED` | 콘텐츠 모더레이션 활성화 | `true` | NO |
| `AUDIT_LOG_ENABLED` | 감사 로그 활성화 | `true` | NO |
| `MAX_AGENTS_PER_USER` | 사용자당 최대 에이전트 수 | `3` | NO |
| `SENTRY_DSN` | 에러 트래킹 DSN | - | NO (권장) |

## 부록 B: 관련 보안 표준 참조

- **OWASP ASVS v4.0**: 인증, 세션 관리, 접근 제어 기준
- **OWASP Top 10 (2021)**: 웹 애플리케이션 보안 위험
- **CWE/SANS Top 25**: 가장 위험한 소프트웨어 약점
- **GDPR**: 유럽 일반 데이터 보호 규정
- **NIST SP 800-63B**: 디지털 인증 가이드라인
- **PostgreSQL Security Best Practices**: 데이터베이스 보안 가이드

---

> **다음 단계**: 이 보안 아키텍처 문서를 기반으로 Phase 0에서 핵심 보안 모듈(인증, 경제 검증, 감사 로그)을 우선 구현한다. 나머지 항목은 각 Phase에서 점진적으로 추가한다.
