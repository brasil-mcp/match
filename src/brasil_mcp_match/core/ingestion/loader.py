"""Postgres loader — usa COPY FROM pra carga rápida da base RF.

Recebe um iterator de dicts (output do parser) + FileType + uma conexão.
Despacha pra função de carga específica por tipo de tabela.

Estratégia de idempotência:
- Cada loader stage as rows pra uma TEMP TABLE via ``COPY ... FROM STDIN``
  (psycopg 3 streaming binário).
- Em seguida, faz ``INSERT INTO ... SELECT ... FROM tmp ON CONFLICT DO NOTHING``
  pra dedupar contra a tabela final.
- Dessa forma releases incrementais (mesmo CNPJ aparecendo em meses diferentes)
  não quebram FK / PK.

Observação: dados extras no row dict são ignorados — só usamos as colunas que
batem com o schema da tabela alvo.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import psycopg

from brasil_mcp_match.core.ingestion.parser import FileType

# --- coluna ordering por tabela ---------------------------------------------
# Mantemos listas explícitas (sem ler do parser) pra deixar 100% claro qual
# coluna vai pro Postgres + o tipo esperado no Python-side.

_EMPRESA_COLS = (
    "cnpj_basico",
    "razao_social",
    "natureza_juridica",
    "qualificacao_responsavel",
    "capital_social",
    "porte_empresa",
    "ente_federativo_responsavel",
)

_ESTABELECIMENTO_COLS = (
    "cnpj_basico",
    "cnpj_ordem",
    "cnpj_dv",
    "identificador_matriz_filial",
    "nome_fantasia",
    "situacao_cadastral",
    "data_situacao_cadastral",
    "motivo_situacao_cadastral",
    "nome_cidade_exterior",
    "pais",
    "data_inicio_atividade",
    "cnae_principal",
    "cnae_secundario",
    "tipo_logradouro",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "uf",
    "municipio",
    "ddd_1",
    "telefone_1",
    "ddd_2",
    "telefone_2",
    "ddd_fax",
    "fax",
    "correio_eletronico",
    "situacao_especial",
    "data_situacao_especial",
)

_SOCIO_COLS = (
    "cnpj_basico",
    "identificador_socio",
    "nome_socio",
    "cnpj_cpf_socio",
    "qualificacao_socio",
    "data_entrada_sociedade",
    "pais",
    "representante_legal",
    "nome_representante",
    "qualificacao_representante",
    "faixa_etaria",
)

_SIMPLES_COLS = (
    "cnpj_basico",
    "opcao_simples",
    "data_opcao_simples",
    "data_exclusao_simples",
    "opcao_mei",
    "data_opcao_mei",
    "data_exclusao_mei",
)

# Reference tables share a 2-column shape (codigo + descricao or nome).
_REF_CODIGO_DESCRICAO = ("codigo", "descricao")


def _staged_copy(
    rows: Iterable[dict[str, Any]],
    conn: psycopg.Connection[Any],
    *,
    final_table: str,
    columns: tuple[str, ...],
    conflict_target: str,
    src_to_dest: dict[str, str] | None = None,
) -> int:
    """Stage rows to a TEMP table via COPY, then upsert into the final table.

    Args:
        rows: Iterable of dicts (parser output). Extra keys are ignored.
        conn: Open psycopg connection.
        final_table: Target table name (e.g. ``empresa``).
        columns: Tuple of column names matching the TEMP table + final table
            schema (in COPY/INSERT order).
        conflict_target: SQL ``ON CONFLICT (...)`` target clause body.
        src_to_dest: Optional mapping of source-dict-key → destination-column.
            Used when parser keys differ from DB column names (e.g. ``codigo``
            in parser → ``codigo_rf`` in ``ref_municipio_rf``).

    Returns the number of rows inserted into the final table.
    """
    src_to_dest = src_to_dest or {}
    # Source keys to look up in each row dict (defaults to column name if
    # no override is given).
    src_keys = tuple(src_to_dest.get(c, c) for c in columns)

    cols_sql = ", ".join(columns)
    tmp_name = f"_stg_{final_table}"

    create_sql = (
        f"CREATE TEMP TABLE {tmp_name} (LIKE {final_table} INCLUDING DEFAULTS) ON COMMIT DROP"
    )
    copy_sql = f"COPY {tmp_name} ({cols_sql}) FROM STDIN"
    insert_sql = (
        f"INSERT INTO {final_table} ({cols_sql}) "
        f"SELECT {cols_sql} FROM {tmp_name} "
        f"ON CONFLICT ({conflict_target}) DO NOTHING"
    )
    drop_sql = f"DROP TABLE {tmp_name}"

    with conn.cursor() as cur:
        cur.execute(create_sql)  # pyright: ignore[reportArgumentType, reportCallIssue]
        with cur.copy(copy_sql) as copy:  # pyright: ignore[reportArgumentType, reportCallIssue]
            for row in rows:
                copy.write_row(tuple(row.get(k) for k in src_keys))

        cur.execute(insert_sql)  # pyright: ignore[reportArgumentType, reportCallIssue]
        inserted = cur.rowcount
        cur.execute(drop_sql)  # pyright: ignore[reportArgumentType, reportCallIssue]
    return inserted if inserted >= 0 else 0


# --- per-table loaders ------------------------------------------------------


def load_empresas(rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]) -> int:
    return _staged_copy(
        rows,
        conn,
        final_table="empresa",
        columns=_EMPRESA_COLS,
        conflict_target="cnpj_basico",
    )


def load_estabelecimentos(rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]) -> int:
    return _staged_copy(
        rows,
        conn,
        final_table="estabelecimento",
        columns=_ESTABELECIMENTO_COLS,
        conflict_target="cnpj_basico, cnpj_ordem",
    )


def load_socios(rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]) -> int:
    # socio table has no natural PK — we still want idempotent re-runs, so we
    # dedupe by (cnpj_basico, nome_socio, cnpj_cpf_socio) via DISTINCT in the
    # staging select. ON CONFLICT can't be used (no unique constraint).
    cols_sql = ", ".join(_SOCIO_COLS)
    tmp_name = "_stg_socio"
    create_sql = f"CREATE TEMP TABLE {tmp_name} (LIKE socio INCLUDING DEFAULTS) ON COMMIT DROP"
    copy_sql = f"COPY {tmp_name} ({cols_sql}) FROM STDIN"
    insert_sql = (
        f"INSERT INTO socio ({cols_sql}) "
        f"SELECT DISTINCT ON (cnpj_basico, nome_socio, COALESCE(cnpj_cpf_socio, '')) "
        f"{cols_sql} "
        f"FROM {tmp_name} stg "
        f"WHERE NOT EXISTS ("
        f"  SELECT 1 FROM socio s "
        f"  WHERE s.cnpj_basico = stg.cnpj_basico "
        f"    AND s.nome_socio = stg.nome_socio "
        f"    AND COALESCE(s.cnpj_cpf_socio, '') = COALESCE(stg.cnpj_cpf_socio, '')"
        f")"
    )
    drop_sql = f"DROP TABLE {tmp_name}"
    with conn.cursor() as cur:
        cur.execute(create_sql)  # pyright: ignore[reportArgumentType, reportCallIssue]
        with cur.copy(copy_sql) as copy:  # pyright: ignore[reportArgumentType, reportCallIssue]
            for row in rows:
                copy.write_row(tuple(row.get(c) for c in _SOCIO_COLS))
        cur.execute(insert_sql)  # pyright: ignore[reportArgumentType, reportCallIssue]
        inserted = cur.rowcount
        cur.execute(drop_sql)  # pyright: ignore[reportArgumentType, reportCallIssue]
    return inserted if inserted >= 0 else 0


def load_simples(rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]) -> int:
    return _staged_copy(
        rows,
        conn,
        final_table="simples_nacional",
        columns=_SIMPLES_COLS,
        conflict_target="cnpj_basico",
    )


def load_ref_cnae(rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]) -> int:
    return _staged_copy(
        rows,
        conn,
        final_table="ref_cnae",
        columns=_REF_CODIGO_DESCRICAO,
        conflict_target="codigo",
    )


def load_ref_natureza_juridica(
    rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]
) -> int:
    return _staged_copy(
        rows,
        conn,
        final_table="ref_natureza_juridica",
        columns=_REF_CODIGO_DESCRICAO,
        conflict_target="codigo",
    )


def load_ref_motivo_situacao(rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]) -> int:
    return _staged_copy(
        rows,
        conn,
        final_table="ref_motivo_situacao",
        columns=_REF_CODIGO_DESCRICAO,
        conflict_target="codigo",
    )


def load_ref_municipio_rf(rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]) -> int:
    # ref_municipio_rf has columns (codigo_rf, nome, uf). Parser yields
    # generic {"codigo": ..., "descricao": ...}. Map codigo→codigo_rf and
    # descricao→nome; uf isn't in the RF reference file (it's resolved later
    # via estabelecimento). We INSERT with uf=NULL.
    return _staged_copy(
        rows,
        conn,
        final_table="ref_municipio_rf",
        columns=("codigo_rf", "nome"),
        conflict_target="codigo_rf",
        src_to_dest={"codigo_rf": "codigo", "nome": "descricao"},
    )


def load_ref_pais(rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]) -> int:
    return _staged_copy(
        rows,
        conn,
        final_table="ref_pais",
        columns=("codigo", "nome"),
        conflict_target="codigo",
        src_to_dest={"nome": "descricao"},
    )


def load_ref_qualificacao_socio(
    rows: Iterable[dict[str, Any]], conn: psycopg.Connection[Any]
) -> int:
    return _staged_copy(
        rows,
        conn,
        final_table="ref_qualificacao_socio",
        columns=_REF_CODIGO_DESCRICAO,
        conflict_target="codigo",
    )


# --- dispatcher --------------------------------------------------------------

_DISPATCH = {
    FileType.EMPRESA: load_empresas,
    FileType.ESTABELECIMENTO: load_estabelecimentos,
    FileType.SOCIO: load_socios,
    FileType.SIMPLES: load_simples,
    FileType.REF_CNAE: load_ref_cnae,
    FileType.REF_NATUREZA: load_ref_natureza_juridica,
    FileType.REF_MOTIVO: load_ref_motivo_situacao,
    FileType.REF_MUNICIPIO: load_ref_municipio_rf,
    FileType.REF_PAIS: load_ref_pais,
    FileType.REF_QUALIFICACAO: load_ref_qualificacao_socio,
}


def load_by_filetype(
    file_type: FileType,
    rows: Iterable[dict[str, Any]],
    conn: psycopg.Connection[Any],
) -> int:
    """Dispatch to the right loader based on FileType. Returns row count."""
    loader = _DISPATCH.get(file_type)
    if loader is None:
        raise NotImplementedError(f"No loader implemented for FileType={file_type!r}")
    return loader(rows, conn)


REF_FILETYPES = frozenset(
    {
        FileType.REF_CNAE,
        FileType.REF_NATUREZA,
        FileType.REF_MOTIVO,
        FileType.REF_MUNICIPIO,
        FileType.REF_PAIS,
        FileType.REF_QUALIFICACAO,
    }
)
