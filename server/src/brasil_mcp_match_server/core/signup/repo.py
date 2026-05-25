"""Signup repository — Protocol + Postgres implementation.

Routes depend on the Protocol so tests can inject an in-memory fake. The
Postgres backend is exercised by integration tests via testcontainers; unit
tests use the fake from ``tests/adapters/conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from brasil_mcp_match_server.core.auth.plan import Plan
from brasil_mcp_match_server.core.signup.models import SignupRequest


class SignupRepo(Protocol):
    """Persistence abstraction for ``signup_request``."""

    def create(
        self,
        *,
        polling_token: str,
        email: str,
        cpf_cnpj: str | None,
        plan: Plan,
        ip_address: str,
        status: str,
        asaas_customer_id: str | None,
        asaas_payment_id: str | None,
        api_key_id: int | None,
        key_plaintext_once: str | None,
        delivered_at: datetime | None,
        created_at: datetime,
        expires_at: datetime,
    ) -> SignupRequest:
        """Insert a new signup_request row, returning the populated record."""
        ...

    def find_by_token(self, polling_token: str) -> SignupRequest | None:
        ...

    def find_by_email(self, email: str) -> SignupRequest | None:
        """First signup_request (oldest) for an email. Used for free-plan
        email-lifetime block. Returns None if no row matches."""
        ...

    def count_free_signups_for_ip_since(self, ip_address: str, since: datetime) -> int:
        """Count free-plan signups from this IP since ``since`` (inclusive).
        Used for the 1-free-per-IP-per-30d block."""
        ...

    def count_signups_for_ip_since(self, ip_address: str, since: datetime) -> int:
        """Count *all* signups from this IP since ``since`` (any plan).
        Used for the 5/hour total block (defense-in-depth alongside slowapi)."""
        ...

    def mark_paid(
        self, polling_token: str, *, api_key_id: int, key_plaintext_once: str
    ) -> SignupRequest | None:
        """Webhook → flip 'pending' to 'paid' and attach the freshly minted key.

        Idempotent: returns the record on first transition, or None if the
        row is missing / already past 'pending' (caller treats as no-op).
        """
        ...

    def find_by_asaas_payment_id(self, asaas_payment_id: str) -> SignupRequest | None:
        ...

    def mark_delivered(self, polling_token: str) -> SignupRequest | None:
        """Status-poll → flip 'paid' to 'delivered', clear key_plaintext_once."""
        ...

    def mark_expired(self, polling_token: str) -> None:
        """Sweep — flag a pending row as expired."""
        ...

    def mark_cancelled(self, polling_token: str) -> None:
        """Refund webhook — flag the row as cancelled."""
        ...


# ---------- Postgres backend ----------


def _row_to_record(row: Any) -> SignupRequest:
    """Convert a psycopg dict_row to a SignupRequest dataclass."""
    return SignupRequest(
        id=row["id"],
        polling_token=str(row["polling_token"]),
        email=row["email"],
        cpf_cnpj=row["cpf_cnpj"],
        plan=Plan(row["plan"]),
        ip_address=row["ip_address"],
        status=row["status"],
        asaas_customer_id=row["asaas_customer_id"],
        asaas_payment_id=row["asaas_payment_id"],
        api_key_id=row["api_key_id"],
        key_plaintext_once=row["key_plaintext_once"],
        delivered_at=row["delivered_at"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


class PostgresSignupRepo:
    """psycopg-backed SignupRepo. Caller manages connection + commit/rollback."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def create(
        self,
        *,
        polling_token: str,
        email: str,
        cpf_cnpj: str | None,
        plan: Plan,
        ip_address: str,
        status: str,
        asaas_customer_id: str | None,
        asaas_payment_id: str | None,
        api_key_id: int | None,
        key_plaintext_once: str | None,
        delivered_at: datetime | None,
        created_at: datetime,
        expires_at: datetime,
    ) -> SignupRequest:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signup_request (
                    polling_token, email, cpf_cnpj, plan, ip_address, status,
                    asaas_customer_id, asaas_payment_id, api_key_id,
                    key_plaintext_once, delivered_at, created_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING id, polling_token, email, cpf_cnpj, plan, ip_address,
                          status, asaas_customer_id, asaas_payment_id, api_key_id,
                          key_plaintext_once, delivered_at, created_at, expires_at
                """,
                (
                    polling_token,
                    email,
                    cpf_cnpj,
                    str(plan),
                    ip_address,
                    status,
                    asaas_customer_id,
                    asaas_payment_id,
                    api_key_id,
                    key_plaintext_once,
                    delivered_at,
                    created_at,
                    expires_at,
                ),
            )
            row = cur.fetchone()
        return _row_to_record(row)

    def find_by_token(self, polling_token: str) -> SignupRequest | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, polling_token, email, cpf_cnpj, plan, ip_address,
                       status, asaas_customer_id, asaas_payment_id, api_key_id,
                       key_plaintext_once, delivered_at, created_at, expires_at
                FROM signup_request WHERE polling_token = %s
                """,
                (polling_token,),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def find_by_email(self, email: str) -> SignupRequest | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, polling_token, email, cpf_cnpj, plan, ip_address,
                       status, asaas_customer_id, asaas_payment_id, api_key_id,
                       key_plaintext_once, delivered_at, created_at, expires_at
                FROM signup_request WHERE email = %s
                ORDER BY created_at ASC LIMIT 1
                """,
                (email,),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def count_free_signups_for_ip_since(self, ip_address: str, since: datetime) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n FROM signup_request
                WHERE ip_address = %s AND plan = 'free' AND created_at >= %s
                """,
                (ip_address, since),
            )
            row = cur.fetchone()
        return int(row["n"]) if row else 0

    def count_signups_for_ip_since(self, ip_address: str, since: datetime) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n FROM signup_request
                WHERE ip_address = %s AND created_at >= %s
                """,
                (ip_address, since),
            )
            row = cur.fetchone()
        return int(row["n"]) if row else 0

    def mark_paid(
        self, polling_token: str, *, api_key_id: int, key_plaintext_once: str
    ) -> SignupRequest | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE signup_request
                SET status = 'paid',
                    api_key_id = %s,
                    key_plaintext_once = %s
                WHERE polling_token = %s AND status = 'pending'
                RETURNING id, polling_token, email, cpf_cnpj, plan, ip_address,
                          status, asaas_customer_id, asaas_payment_id, api_key_id,
                          key_plaintext_once, delivered_at, created_at, expires_at
                """,
                (api_key_id, key_plaintext_once, polling_token),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def find_by_asaas_payment_id(self, asaas_payment_id: str) -> SignupRequest | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, polling_token, email, cpf_cnpj, plan, ip_address,
                       status, asaas_customer_id, asaas_payment_id, api_key_id,
                       key_plaintext_once, delivered_at, created_at, expires_at
                FROM signup_request WHERE asaas_payment_id = %s LIMIT 1
                """,
                (asaas_payment_id,),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def mark_delivered(self, polling_token: str) -> SignupRequest | None:
        now = datetime.now(UTC)
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE signup_request
                SET status = 'delivered',
                    delivered_at = %s,
                    key_plaintext_once = NULL
                WHERE polling_token = %s AND status = 'paid'
                RETURNING id, polling_token, email, cpf_cnpj, plan, ip_address,
                          status, asaas_customer_id, asaas_payment_id, api_key_id,
                          key_plaintext_once, delivered_at, created_at, expires_at
                """,
                (now, polling_token),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def mark_expired(self, polling_token: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE signup_request SET status = 'expired'
                WHERE polling_token = %s AND status = 'pending'
                """,
                (polling_token,),
            )

    def mark_cancelled(self, polling_token: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE signup_request SET status = 'cancelled'
                WHERE polling_token = %s
                """,
                (polling_token,),
            )
