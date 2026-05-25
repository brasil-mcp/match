"""API key persistence — insert/revoke operations.

Lookup of API keys for auth happens elsewhere (the ServiceContext's
``api_key_lookup`` callable). This module covers the *write* side that the
signup + webhook handlers need.
"""

from __future__ import annotations

from typing import Any, Protocol

from brasil_mcp_match_server.core.auth.api_key import ApiKeyRecord
from brasil_mcp_match_server.core.auth.plan import Plan, get_plan_config


class ApiKeyRepo(Protocol):
    """Persistence abstraction for ``api_key`` rows (write side)."""

    def insert(
        self,
        *,
        key_hash: str,
        plan: Plan,
        customer_email: str | None,
    ) -> ApiKeyRecord:
        """Insert a new api_key row, returning the populated record (with id)."""
        ...

    def revoke(self, api_key_id: int) -> None:
        """Mark an api_key row as revoked (sets revoked_at = now())."""
        ...


class PostgresApiKeyRepo:
    """psycopg-backed ApiKeyRepo. Caller manages connection + commit/rollback."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def insert(
        self,
        *,
        key_hash: str,
        plan: Plan,
        customer_email: str | None,
    ) -> ApiKeyRecord:
        cfg = get_plan_config(plan)
        # quota_per_month is NOT NULL in the schema. For unlimited
        # (enterprise) we store the placeholder ``-1`` matching the in-code
        # convention (see PlanConfig.unlimited).
        quota = cfg.monthly_quota
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO api_key (key_hash, plan, customer_email, quota_per_month)
                VALUES (%s, %s, %s, %s)
                RETURNING id, key_hash, plan, customer_email, revoked_at
                """,
                (key_hash, str(plan), customer_email, quota),
            )
            row = cur.fetchone()
        return ApiKeyRecord(
            id=row["id"],
            key_hash=row["key_hash"],
            plan=Plan(row["plan"]),
            customer_email=row["customer_email"],
            is_revoked=row["revoked_at"] is not None,
        )

    def revoke(self, api_key_id: int) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE api_key SET revoked_at = NOW() WHERE id = %s",
                (api_key_id,),
            )
