"""Tests for ingestion.manifest — full mock of the psycopg connect() ctx mgr."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from brasil_mcp_match.core.ingestion import manifest as manifest_mod
from brasil_mcp_match.core.ingestion.manifest import (
    base_updated_date,
    complete_run,
    fail_run,
    latest_successful,
    start_run,
)


@pytest.fixture
def mock_connect(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch `connect()` to yield a controllable MagicMock connection.

    Returns the cursor — tests configure `cursor.fetchone.return_value`.
    Also exposes the connection via `cursor._conn` for commit assertions.
    """
    cursor = MagicMock(name="cursor")
    cursor_cm = MagicMock(name="cursor_cm")
    cursor_cm.__enter__.return_value = cursor
    cursor_cm.__exit__.return_value = False

    conn = MagicMock(name="connection")
    conn.cursor.return_value = cursor_cm

    @contextmanager
    def fake_connect() -> Any:
        yield conn

    monkeypatch.setattr(manifest_mod, "connect", fake_connect)
    cursor._conn = conn  # expose for assertions
    return cursor


# ------------ start_run ------------


def test_start_run_inserts_and_returns_id(mock_connect: MagicMock) -> None:
    mock_connect.fetchone.return_value = {"id": 42}
    run_id = start_run("202604")
    assert run_id == 42

    sql, params = mock_connect.execute.call_args[0]
    assert "INSERT INTO ingestion_manifest" in sql
    assert "ON CONFLICT (rf_release) DO UPDATE" in sql
    assert "RETURNING id" in sql
    assert params == ("202604",)
    mock_connect._conn.commit.assert_called_once()


# ------------ complete_run ------------


def test_complete_run_updates_status_and_counts(mock_connect: MagicMock) -> None:
    complete_run(run_id=7, file_count=30, row_count=12_000_000)

    sql, params = mock_connect.execute.call_args[0]
    assert "UPDATE ingestion_manifest" in sql
    assert "status = 'success'" in sql
    assert params == (30, 12_000_000, 7)
    mock_connect._conn.commit.assert_called_once()


# ------------ fail_run ------------


def test_fail_run_persists_error_message(mock_connect: MagicMock) -> None:
    fail_run(run_id=99, error="Connection reset by peer")

    sql, params = mock_connect.execute.call_args[0]
    assert "UPDATE ingestion_manifest" in sql
    assert "status = 'failed'" in sql
    assert params == ("Connection reset by peer", 99)
    mock_connect._conn.commit.assert_called_once()


# ------------ latest_successful ------------


def test_latest_successful_returns_none_when_no_runs(mock_connect: MagicMock) -> None:
    mock_connect.fetchone.return_value = None
    assert latest_successful() is None


def test_latest_successful_hydrates_run(mock_connect: MagicMock) -> None:
    mock_connect.fetchone.return_value = {
        "id": 12,
        "rf_release": "202604",
        "started_at": datetime(2026, 5, 1, 3, 0, 0),
        "completed_at": datetime(2026, 5, 1, 4, 15, 0),
        "status": "success",
        "file_count": 30,
        "row_count": 60_000_000,
        "error_message": None,
    }
    run = latest_successful()
    assert run is not None
    assert run.id == 12
    assert run.rf_release == "202604"
    assert run.completed_at == datetime(2026, 5, 1, 4, 15, 0)
    assert run.status == "success"
    assert run.file_count == 30
    assert run.row_count == 60_000_000
    assert run.error_message is None


# ------------ base_updated_date ------------


def test_base_updated_date_none_when_no_runs(mock_connect: MagicMock) -> None:
    mock_connect.fetchone.return_value = None
    assert base_updated_date() is None


def test_base_updated_date_parses_yyyymm(mock_connect: MagicMock) -> None:
    mock_connect.fetchone.return_value = {
        "id": 1,
        "rf_release": "202604",
        "started_at": datetime(2026, 5, 1),
        "completed_at": datetime(2026, 5, 1, 1, 0, 0),
        "status": "success",
        "file_count": 30,
        "row_count": 1_000,
        "error_message": None,
    }
    assert base_updated_date() == date(2026, 4, 1)
