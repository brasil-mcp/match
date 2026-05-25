"""Tests for the sócio + qualificacao methods on PostgresCnpjRepo.

Mocks the psycopg connection (no live DB needed). Asserts the SQL templates
use parameterized placeholders only and the rows produced by the dict_row
factory get mapped into ``SocioRecord`` correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from brasil_mcp_match_server.core.repository.cnpj_repo import SocioRecord
from brasil_mcp_match_server.core.repository.postgres_repo import (
    _FIND_SOCIOS_BY_CNPJ_BASICO_SQL,
    _QUALIFICACAO_EXISTS_SQL,
    PostgresCnpjRepo,
)

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _conn_with_cursor(
    *,
    fetchall_result: list[dict] | None = None,
    fetchone_result: dict | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build a MagicMock psycopg connection where ``cursor()`` returns a
    context-manager yielding a cursor whose ``fetchall``/``fetchone`` are
    pre-canned."""
    cur = MagicMock(name="cursor")
    cur.fetchall.return_value = fetchall_result or []
    cur.fetchone.return_value = fetchone_result
    cur_ctx = MagicMock()
    cur_ctx.__enter__.return_value = cur
    cur_ctx.__exit__.return_value = False
    conn = MagicMock(name="conn")
    conn.cursor.return_value = cur_ctx
    return conn, cur


# ============================================================================
# find_socios_by_cnpj_basico
# ============================================================================


def test_find_socios_returns_empty_when_no_rows() -> None:
    conn, cur = _conn_with_cursor(fetchall_result=[])
    repo = PostgresCnpjRepo(conn)
    rows = repo.find_socios_by_cnpj_basico("12345678")
    assert rows == []
    # Parameterized — the basico is bound, not concatenated.
    assert cur.execute.call_args[0][1] == ("12345678",)


def test_find_socios_maps_pf_row_to_record() -> None:
    conn, _cur = _conn_with_cursor(
        fetchall_result=[
            {
                "nome_socio": "JOSE PINHEIRO SILVA",
                "cnpj_cpf_socio": "***123456**",
                "identificador_socio": 1,
                "qualificacao_socio": 10,
            }
        ]
    )
    repo = PostgresCnpjRepo(conn)
    rows = repo.find_socios_by_cnpj_basico("33000167")
    assert len(rows) == 1
    rec = rows[0]
    assert isinstance(rec, SocioRecord)
    assert rec.nome_socio == "JOSE PINHEIRO SILVA"
    assert rec.cnpj_cpf_socio == "***123456**"
    assert rec.identificador_socio == 1
    assert rec.qualificacao_socio == 10


def test_find_socios_maps_pj_row_to_record() -> None:
    conn, _cur = _conn_with_cursor(
        fetchall_result=[
            {
                "nome_socio": "HOLDING ENERGETICA S.A.",
                "cnpj_cpf_socio": "11444777000161",
                "identificador_socio": 2,
                "qualificacao_socio": 22,
            }
        ]
    )
    repo = PostgresCnpjRepo(conn)
    rows = repo.find_socios_by_cnpj_basico("33000167")
    assert len(rows) == 1
    assert rows[0].identificador_socio == 2
    assert rows[0].cnpj_cpf_socio == "11444777000161"


def test_find_socios_handles_null_fields() -> None:
    conn, _cur = _conn_with_cursor(
        fetchall_result=[
            {
                "nome_socio": "FOREIGN PARTNER",
                "cnpj_cpf_socio": None,
                "identificador_socio": None,
                "qualificacao_socio": None,
            }
        ]
    )
    repo = PostgresCnpjRepo(conn)
    rows = repo.find_socios_by_cnpj_basico("33000167")
    assert rows[0].cnpj_cpf_socio is None
    assert rows[0].identificador_socio is None
    assert rows[0].qualificacao_socio is None


def test_find_socios_multiple_rows() -> None:
    conn, _cur = _conn_with_cursor(
        fetchall_result=[
            {
                "nome_socio": "A",
                "cnpj_cpf_socio": "***111111**",
                "identificador_socio": 1,
                "qualificacao_socio": 10,
            },
            {
                "nome_socio": "B",
                "cnpj_cpf_socio": "***222222**",
                "identificador_socio": 1,
                "qualificacao_socio": 22,
            },
            {
                "nome_socio": "C HOLDING",
                "cnpj_cpf_socio": "11111111000111",
                "identificador_socio": 2,
                "qualificacao_socio": 22,
            },
        ]
    )
    repo = PostgresCnpjRepo(conn)
    rows = repo.find_socios_by_cnpj_basico("33000167")
    assert len(rows) == 3
    assert [r.identificador_socio for r in rows] == [1, 1, 2]


def test_find_socios_sql_is_parameterized() -> None:
    """The SQL template must not interpolate the basico — only %s placeholders."""
    assert "%s" in _FIND_SOCIOS_BY_CNPJ_BASICO_SQL
    assert "{" not in _FIND_SOCIOS_BY_CNPJ_BASICO_SQL
    assert "}" not in _FIND_SOCIOS_BY_CNPJ_BASICO_SQL
    # Must select only the four fields we use — nothing extra (defense
    # against accidentally leaking representante_legal etc. via the matcher).
    sql_upper = _FIND_SOCIOS_BY_CNPJ_BASICO_SQL.upper()
    assert "REPRESENTANTE_LEGAL" not in sql_upper
    assert "NOME_REPRESENTANTE" not in sql_upper
    assert "FAIXA_ETARIA" not in sql_upper


# ============================================================================
# qualificacao_codigo_exists
# ============================================================================


def test_qualificacao_codigo_exists_true() -> None:
    conn, cur = _conn_with_cursor(fetchone_result={"?column?": 1})
    repo = PostgresCnpjRepo(conn)
    assert repo.qualificacao_codigo_exists(10) is True
    assert cur.execute.call_args[0][1] == (10,)


def test_qualificacao_codigo_exists_false() -> None:
    conn, _cur = _conn_with_cursor(fetchone_result=None)
    repo = PostgresCnpjRepo(conn)
    assert repo.qualificacao_codigo_exists(99999) is False


def test_qualificacao_sql_is_parameterized() -> None:
    assert "%s" in _QUALIFICACAO_EXISTS_SQL
    assert "{" not in _QUALIFICACAO_EXISTS_SQL
    assert "}" not in _QUALIFICACAO_EXISTS_SQL
