"""Parse dos CSVs da base Receita Federal — streaming, sem carregar tudo em memória.

A RF distribui arquivos CSV dentro de zips, encoding latin-1, separador ";",
sem header. Cada tipo de arquivo (Empresas, Estabelecimentos, Socios, Simples,
plus tabelas auxiliares) tem schema fixo documentado por colunas posicionais.

Este parser:
- Recebe um Path pra um zip baixado.
- Detecta o tipo do arquivo (Empresa/Estabelecimento/Socio/Simples/auxiliar).
- Itera linhas como dicts tipados, fazendo:
  - decode latin-1 → utf-8
  - strip de aspas duplicadas (RF às vezes serializa errado)
  - conversão de datas YYYYMMDD → date
  - conversão de capital_social (virgula → ponto decimal)
  - normalização de NULLs (string vazia ou "0" pra None onde apropriado)

Não toca em Postgres — quem consome é o loader.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

# Schemas posicionais conforme docs RF (e br-acc, libercapital, sinarc).

EMPRESAS_COLS = [
    "cnpj_basico",
    "razao_social",
    "natureza_juridica",
    "qualificacao_responsavel",
    "capital_social",
    "porte_empresa",
    "ente_federativo_responsavel",
]

ESTABELECIMENTOS_COLS = [
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
]

SOCIOS_COLS = [
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
]

SIMPLES_COLS = [
    "cnpj_basico",
    "opcao_simples",
    "data_opcao_simples",
    "data_exclusao_simples",
    "opcao_mei",
    "data_opcao_mei",
    "data_exclusao_mei",
]


class FileType(StrEnum):
    EMPRESA = "empresa"
    ESTABELECIMENTO = "estabelecimento"
    SOCIO = "socio"
    SIMPLES = "simples"
    REF_CNAE = "ref_cnae"
    REF_NATUREZA = "ref_natureza_juridica"
    REF_MOTIVO = "ref_motivo_situacao"
    REF_MUNICIPIO = "ref_municipio_rf"
    REF_PAIS = "ref_pais"
    REF_QUALIFICACAO = "ref_qualificacao_socio"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FileInfo:
    type: FileType
    columns: list[str] | None  # None for ref tables (2-col code/desc)


# RF uses filenames like "K3241.K03200Y0.D40510.EMPRECSV" — token detection
# matches the suffix pattern. Order matters: check most specific first.
_FILENAME_PATTERNS: tuple[tuple[str, FileInfo], ...] = (
    ("ESTABELE", FileInfo(FileType.ESTABELECIMENTO, ESTABELECIMENTOS_COLS)),
    ("EMPRECSV", FileInfo(FileType.EMPRESA, EMPRESAS_COLS)),
    ("EMPRESAS", FileInfo(FileType.EMPRESA, EMPRESAS_COLS)),
    ("SOCIOCSV", FileInfo(FileType.SOCIO, SOCIOS_COLS)),
    ("SOCIOS", FileInfo(FileType.SOCIO, SOCIOS_COLS)),
    ("SIMPLES", FileInfo(FileType.SIMPLES, SIMPLES_COLS)),
    ("CNAECSV", FileInfo(FileType.REF_CNAE, None)),
    ("NATJUCSV", FileInfo(FileType.REF_NATUREZA, None)),
    ("MOTICSV", FileInfo(FileType.REF_MOTIVO, None)),
    ("MUNICCSV", FileInfo(FileType.REF_MUNICIPIO, None)),
    ("PAISCSV", FileInfo(FileType.REF_PAIS, None)),
    ("QUALSCSV", FileInfo(FileType.REF_QUALIFICACAO, None)),
)


def detect_file_type(filename: str) -> FileInfo:
    """Identify what kind of RF file this is from the filename."""
    upper = filename.upper()
    for token, info in _FILENAME_PATTERNS:
        if token in upper:
            return info
    return FileInfo(FileType.UNKNOWN, None)


def _parse_date(value: str) -> date | None:
    """Parse YYYYMMDD strings (RF format). Returns None for '0' / '00000000' / empty."""
    if not value or value in ("0", "00000000"):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def _parse_decimal(value: str) -> Decimal | None:
    """Parse a numeric string with comma decimal separator (BR format)."""
    if not value or value == "0":
        return Decimal("0") if value == "0" else None
    cleaned = value.replace(",", ".").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _nullify(value: str) -> str | None:
    """Empty string → None; otherwise the value."""
    return value if value else None


_DATE_COLUMNS = frozenset(
    {
        "data_situacao_cadastral",
        "data_inicio_atividade",
        "data_situacao_especial",
        "data_entrada_sociedade",
        "data_opcao_simples",
        "data_exclusao_simples",
        "data_opcao_mei",
        "data_exclusao_mei",
    }
)
_DECIMAL_COLUMNS = frozenset({"capital_social"})


def _coerce_row(row: list[str], columns: list[str]) -> dict[str, object]:
    """Transform a positional CSV row into a typed dict per RF conventions."""
    out: dict[str, object] = {}
    for col, raw in zip(columns, row, strict=False):
        if col in _DATE_COLUMNS:
            out[col] = _parse_date(raw)
        elif col in _DECIMAL_COLUMNS:
            out[col] = _parse_decimal(raw)
        else:
            out[col] = _nullify(raw)
    return out


def parse_zip(
    zip_path: Path, columns_override: list[str] | None = None
) -> Iterator[dict[str, object]]:
    """Stream-parse a single RF zip. Yields one typed dict per CSV row.

    Detects file type from the inner CSV filename. For reference tables
    (`columns_override` not provided), uses a generic 2-column {codigo, descricao}.
    """
    with zipfile.ZipFile(zip_path) as zf:
        for inner_name in zf.namelist():
            info = detect_file_type(inner_name)
            columns = columns_override or info.columns
            if columns is None and info.type.value.startswith("ref_"):
                columns = ["codigo", "descricao"]
            if columns is None:
                continue  # unknown — skip

            with zf.open(inner_name) as raw_fh:
                text = io.TextIOWrapper(raw_fh, encoding="latin-1", newline="")
                reader = csv.reader(text, delimiter=";", quotechar='"')
                for row in reader:
                    if not row or all(not cell for cell in row):
                        continue
                    yield _coerce_row(row, columns)


def parse_csv_bytes(
    data: bytes, columns: list[str], encoding: str = "latin-1"
) -> Iterator[dict[str, object]]:
    """Parse a raw CSV byte stream (no zip wrapper). Useful for tests."""
    text = io.TextIOWrapper(io.BytesIO(data), encoding=encoding, newline="")
    reader = csv.reader(text, delimiter=";", quotechar='"')
    for row in reader:
        if not row or all(not cell for cell in row):
            continue
        yield _coerce_row(row, columns)
