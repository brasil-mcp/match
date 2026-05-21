"""Tests for audit log — pure fns + cursor mock."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

from brasil_mcp_match.core.audit.log import (
    build_entry,
    hash_input,
    insert,
    new_query_id,
    older_than,
)


def test_new_query_id_is_uuid() -> None:
    import uuid as uuid_mod

    qid = new_query_id()
    uuid_mod.UUID(qid)  # would raise if not a valid UUID


def test_new_query_id_unique() -> None:
    a = new_query_id()
    b = new_query_id()
    assert a != b


def test_hash_input_deterministic() -> None:
    a = hash_input({"cnpj": "12345678", "nome": "X"})
    b = hash_input({"nome": "X", "cnpj": "12345678"})  # different key order
    assert a == b  # sort_keys → deterministic


def test_hash_input_differs_for_different_payload() -> None:
    a = hash_input({"cnpj": "12345678"})
    b = hash_input({"cnpj": "99999999"})
    assert a != b


def test_build_entry_fills_defaults() -> None:
    entry = build_entry(
        api_key_hash="x" * 64,
        tool="match_razao_social",
        payload={"cnpj": "12345678", "nome": "TEST"},
        cnpj_basico="12345678",
        response_summary={"match": True, "confidence": 0.92},
        base_updated_at=date(2026, 5, 1),
    )
    assert entry.tool == "match_razao_social"
    assert entry.cnpj_basico == "12345678"
    assert entry.response_summary == {"match": True, "confidence": 0.92}
    assert entry.input_hash == hash_input({"cnpj": "12345678", "nome": "TEST"})
    assert entry.ip_address is None
    assert entry.latency_ms is None
    # query_id auto-generated
    assert entry.query_id


def test_build_entry_explicit_query_id() -> None:
    qid = "test-fixed-id"
    entry = build_entry(
        api_key_hash="x" * 64,
        tool="check_situacao",
        payload={},
        cnpj_basico="12345678",
        response_summary={},
        base_updated_at=date(2026, 5, 1),
        query_id=qid,
    )
    assert entry.query_id == qid


def test_insert_calls_cursor_with_correct_columns() -> None:
    cursor = MagicMock()
    entry = build_entry(
        api_key_hash="h" * 64,
        tool="match_uf",
        payload={"cnpj": "12345678", "uf": "SP"},
        cnpj_basico="12345678",
        response_summary={"match": True},
        base_updated_at=date(2026, 5, 1),
        ip_address="203.0.113.1",
        latency_ms=42,
    )
    insert(entry, cursor)
    cursor.execute.assert_called_once()
    sql, params = cursor.execute.call_args[0]
    assert "INSERT INTO audit_log" in sql
    assert params[0] == entry.query_id
    assert params[1] == entry.api_key_hash
    assert params[2] == "match_uf"
    assert params[3] == entry.input_hash
    assert params[4] == "12345678"
    # response_summary serialized as JSON
    import json as json_mod

    assert json_mod.loads(params[5]) == {"match": True}
    assert params[6] == date(2026, 5, 1)
    assert params[7] == "203.0.113.1"
    assert params[8] == 42


def test_older_than_deletes_old_entries() -> None:
    cursor = MagicMock()
    cursor.rowcount = 42
    cutoff = datetime(2025, 11, 1)
    deleted = older_than(cutoff, cursor)
    cursor.execute.assert_called_once()
    sql, params = cursor.execute.call_args[0]
    assert "DELETE FROM audit_log" in sql
    assert params == (cutoff,)
    assert deleted == 42


def test_older_than_zero_rowcount_returns_zero() -> None:
    cursor = MagicMock()
    cursor.rowcount = None  # psycopg returns None if no rows
    deleted = older_than(datetime(2025, 1, 1), cursor)
    assert deleted == 0


# ------------ Privacy contract ------------


def test_audit_entry_does_not_store_payload_raw() -> None:
    """The payload should be hashed, never stored in plaintext."""
    secret_payload = {"cnpj": "12345678", "nome_socio": "JOSE SECRETO"}
    entry = build_entry(
        api_key_hash="x" * 64,
        tool="match_socio",
        payload=secret_payload,
        cnpj_basico="12345678",
        response_summary={"match": False},
        base_updated_at=date(2026, 5, 1),
    )
    # The hash should not contain the secret value
    assert "JOSE SECRETO" not in entry.input_hash
    assert "12345678" not in entry.input_hash[:30]  # sha256 won't have substring
    # input_hash is a 64-char hex
    assert len(entry.input_hash) == 64
    assert all(c in "0123456789abcdef" for c in entry.input_hash)


def test_audit_entry_does_not_store_api_key_plaintext() -> None:
    plaintext_key = "brasilmcp_SUPERSECRET"
    from brasil_mcp_match.core.auth.api_key import hash_key

    h = hash_key(plaintext_key)
    entry = build_entry(
        api_key_hash=h,
        tool="x",
        payload={},
        cnpj_basico="00000000",
        response_summary={},
        base_updated_at=date(2026, 1, 1),
    )
    assert "SUPERSECRET" not in entry.api_key_hash
