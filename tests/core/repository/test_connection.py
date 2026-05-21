"""Tests for repository.connection — env handling + context manager (no real DB)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from brasil_mcp_match.core.repository.connection import connect, get_database_url

# ------------ get_database_url ------------


def test_get_database_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var → default local URL."""
    monkeypatch.delenv("BRASIL_MCP_MATCH_DATABASE_URL", raising=False)
    url = get_database_url()
    assert url.startswith("postgresql://")
    assert "localhost:5432" in url
    assert "brasil_mcp_match" in url


def test_get_database_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BRASIL_MCP_MATCH_DATABASE_URL",
        "postgresql://prod_user:secret@db.prod:5432/brasilmcp_match",
    )
    url = get_database_url()
    assert url == "postgresql://prod_user:secret@db.prod:5432/brasilmcp_match"


# ------------ connect() context manager ------------


def test_connect_yields_psycopg_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() opens a psycopg connection with dict_row and yields it."""
    monkeypatch.setenv("BRASIL_MCP_MATCH_DATABASE_URL", "postgresql://x:y@z:5432/d")

    fake_conn = MagicMock(name="psycopg_connection")
    fake_cm = MagicMock(name="psycopg_cm")
    fake_cm.__enter__.return_value = fake_conn
    fake_cm.__exit__.return_value = False

    with patch("brasil_mcp_match.core.repository.connection.psycopg") as psycopg_mod:
        psycopg_mod.connect.return_value = fake_cm
        with connect() as conn:
            assert conn is fake_conn

    # psycopg.connect was called with the env URL + dict_row factory.
    psycopg_mod.connect.assert_called_once()
    call_args = psycopg_mod.connect.call_args
    assert call_args[0][0] == "postgresql://x:y@z:5432/d"
    # row_factory keyword
    assert "row_factory" in call_args[1]


def test_connect_propagates_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exceptions inside the with-block propagate cleanly."""
    monkeypatch.setenv("BRASIL_MCP_MATCH_DATABASE_URL", "postgresql://x:y@z:5432/d")

    fake_conn = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_conn
    fake_cm.__exit__.return_value = False

    with patch("brasil_mcp_match.core.repository.connection.psycopg") as psycopg_mod:
        psycopg_mod.connect.return_value = fake_cm
        with pytest.raises(RuntimeError, match="boom"):
            with connect():
                raise RuntimeError("boom")
