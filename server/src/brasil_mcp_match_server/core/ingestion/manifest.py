"""Manifest log da ingestão — qual release RF foi carregado, quando, status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from brasil_mcp_match_server.core.repository.connection import connect


@dataclass(frozen=True, slots=True)
class IngestionRun:
    id: int
    rf_release: str  # YYYYMM
    started_at: datetime
    completed_at: datetime | None
    status: str  # "running" | "success" | "failed"
    file_count: int | None
    row_count: int | None
    error_message: str | None


def start_run(rf_release: str) -> int:
    """Insert a 'running' manifest row; returns the new id."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_manifest (rf_release, status)
            VALUES (%s, 'running')
            ON CONFLICT (rf_release) DO UPDATE
                SET started_at = excluded.started_at,
                    status = 'running',
                    completed_at = NULL,
                    file_count = NULL,
                    row_count = NULL,
                    error_message = NULL
            RETURNING id
            """,
            (rf_release,),
        )
        row = cur.fetchone()
        assert row is not None
        conn.commit()
        return int(row["id"])


def complete_run(run_id: int, file_count: int, row_count: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_manifest
            SET status = 'success',
                completed_at = now(),
                file_count = %s,
                row_count = %s
            WHERE id = %s
            """,
            (file_count, row_count, run_id),
        )
        conn.commit()


def fail_run(run_id: int, error: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_manifest
            SET status = 'failed',
                completed_at = now(),
                error_message = %s
            WHERE id = %s
            """,
            (error, run_id),
        )
        conn.commit()


def latest_successful() -> IngestionRun | None:
    """Return the most recent successful ingestion run, or None if none yet."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, rf_release, started_at, completed_at, status,
                   file_count, row_count, error_message
            FROM ingestion_manifest
            WHERE status = 'success'
            ORDER BY completed_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            return None
        return IngestionRun(
            id=row["id"],
            rf_release=row["rf_release"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            status=row["status"],
            file_count=row["file_count"],
            row_count=row["row_count"],
            error_message=row["error_message"],
        )


def base_updated_date() -> date | None:
    """Return the date (rf_release as date) of the most recent successful ingestion."""
    run = latest_successful()
    if run is None:
        return None
    # rf_release is "YYYYMM" — convert to first day of month
    return date(int(run.rf_release[:4]), int(run.rf_release[4:]), 1)
