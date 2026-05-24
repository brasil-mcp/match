"""Tests for ingestion parser — synthetic RF CSV bytes."""

from __future__ import annotations

import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from brasil_mcp_match_server.core.ingestion.parser import (
    EMPRESAS_COLS,
    ESTABELECIMENTOS_COLS,
    SOCIOS_COLS,
    FileType,
    detect_file_type,
    parse_csv_bytes,
    parse_zip,
)

# ------------ File-type detection ------------


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("K3241.K03200Y0.D40510.EMPRECSV", FileType.EMPRESA),
        ("K3241.K03200Y2.D40510.ESTABELE", FileType.ESTABELECIMENTO),
        ("K3241.K03200Y6.D40510.SOCIOCSV", FileType.SOCIO),
        ("K3241.K03200Y9.D40510.SIMPLES.CSV.D40510", FileType.SIMPLES),
        ("F.K03200$Z.D40510.CNAECSV", FileType.REF_CNAE),
        ("F.K03200$Z.D40510.NATJUCSV", FileType.REF_NATUREZA),
        ("F.K03200$Z.D40510.MOTICSV", FileType.REF_MOTIVO),
        ("F.K03200$Z.D40510.MUNICCSV", FileType.REF_MUNICIPIO),
        ("F.K03200$Z.D40510.PAISCSV", FileType.REF_PAIS),
        ("F.K03200$Z.D40510.QUALSCSV", FileType.REF_QUALIFICACAO),
        ("readme.txt", FileType.UNKNOWN),
        ("", FileType.UNKNOWN),
    ],
)
def test_detect_file_type(filename: str, expected: FileType) -> None:
    info = detect_file_type(filename)
    assert info.type == expected


# ------------ Empresa CSV (positional) ------------


def test_parse_empresa_row_typed() -> None:
    # cnpj_basico;razao_social;natureza_juridica;qualif_resp;capital_social;porte;ente_fed
    csv_bytes = (
        b'"12345678";"PETROLEO BRASILEIRO S A PETROBRAS";"2011";"05";"205431960490,00";"05";""\n'
    )
    rows = list(parse_csv_bytes(csv_bytes, EMPRESAS_COLS))
    assert len(rows) == 1
    row = rows[0]
    assert row["cnpj_basico"] == "12345678"
    assert row["razao_social"] == "PETROLEO BRASILEIRO S A PETROBRAS"
    assert row["natureza_juridica"] == "2011"
    assert row["capital_social"] == Decimal("205431960490.00")
    assert row["porte_empresa"] == "05"
    # ente_fed empty → None
    assert row["ente_federativo_responsavel"] is None


def test_parse_empresa_capital_zero_preserved() -> None:
    csv_bytes = b'"99999999";"EMPRESA X";"2135";"05";"0";"01";""\n'
    row = next(parse_csv_bytes(csv_bytes, EMPRESAS_COLS))
    assert row["capital_social"] == Decimal("0")


def test_parse_empresa_capital_invalid_returns_none() -> None:
    csv_bytes = b'"99999999";"X";"2135";"05";"abc";"01";""\n'
    row = next(parse_csv_bytes(csv_bytes, EMPRESAS_COLS))
    assert row["capital_social"] is None


# ------------ Estabelecimento CSV (29 cols, dates inclusive) ------------


def test_parse_estabelecimento_with_dates() -> None:
    # All 29 columns
    fields = [
        "12345678",
        "0001",
        "95",
        "1",
        "POSTO DA ESQUINA",
        "2",  # situacao_cadastral
        "20240115",  # data_situacao_cadastral
        "0",
        "",
        "",
        "20100201",  # data_inicio_atividade
        "4731800",  # cnae_principal
        "4732600,4730000",
        "AVENIDA",
        "PAULISTA",
        "1500",
        "",
        "BELA VISTA",
        "01310200",
        "SP",
        "7107",
        "11",
        "30001000",
        "",
        "",
        "",
        "",
        "contato@posto.com.br",
        "",
        "0",  # data_situacao_especial = 0 → None
    ]
    csv_bytes = (";".join(f'"{f}"' for f in fields) + "\n").encode("latin-1")
    row = next(parse_csv_bytes(csv_bytes, ESTABELECIMENTOS_COLS))
    assert row["cnpj_basico"] == "12345678"
    assert row["cnpj_ordem"] == "0001"
    assert row["cnpj_dv"] == "95"
    assert row["situacao_cadastral"] == "2"
    assert row["data_situacao_cadastral"] == date(2024, 1, 15)
    assert row["data_inicio_atividade"] == date(2010, 2, 1)
    assert row["data_situacao_especial"] is None  # "0" → None
    assert row["cep"] == "01310200"
    assert row["uf"] == "SP"
    assert row["correio_eletronico"] == "contato@posto.com.br"


# ------------ Socio CSV (CPF mascarado) ------------


def test_parse_socio_cpf_mascarado_preserved() -> None:
    fields = [
        "12345678",
        "2",  # PF
        "JOSE DA SILVA SAURO",
        "***123456**",  # CPF mascarado pela RF
        "49",
        "20100201",
        "76",
        "",
        "",
        "",
        "4",  # faixa_etaria
    ]
    csv_bytes = (";".join(f'"{f}"' for f in fields) + "\n").encode("latin-1")
    row = next(parse_csv_bytes(csv_bytes, SOCIOS_COLS))
    assert row["nome_socio"] == "JOSE DA SILVA SAURO"
    assert row["cnpj_cpf_socio"] == "***123456**"
    assert row["data_entrada_sociedade"] == date(2010, 2, 1)


# ------------ Edge cases: malformed input ------------


def test_parser_skips_empty_lines() -> None:
    csv_bytes = (
        b'"12345678";"X";"2135";"05";"0";"01";""\n\n\n"99999999";"Y";"2135";"05";"0";"01";""\n'
    )
    rows = list(parse_csv_bytes(csv_bytes, EMPRESAS_COLS))
    assert len(rows) == 2
    assert rows[0]["cnpj_basico"] == "12345678"
    assert rows[1]["cnpj_basico"] == "99999999"


def test_parser_handles_latin1_encoding() -> None:
    # RF distributes latin-1; "ÇÃO" must roundtrip
    csv_bytes = '"99999999";"AÇÃO LTDA";"2135";"05";"0";"01";""\n'.encode("latin-1")
    row = next(parse_csv_bytes(csv_bytes, EMPRESAS_COLS))
    assert row["razao_social"] == "AÇÃO LTDA"


def test_parser_invalid_date_returns_none() -> None:
    # Invalid date like 99999999
    fields = ["12345678", "X", "2135", "05", "0", "01", ""]
    csv_bytes = (";".join(f'"{f}"' for f in fields) + "\n").encode("latin-1")
    row = next(parse_csv_bytes(csv_bytes, EMPRESAS_COLS))
    assert row["razao_social"] == "X"


def test_parser_malformed_date_returns_none() -> None:
    """Date that parses as 8 chars but is logically invalid (e.g., month 13) → None."""
    # data_situacao_cadastral position 7 (index 6) on ESTABELECIMENTOS
    fields = ["12345678", "0001", "95", "1", "X", "2", "20241301"]  # month 13 invalid
    # Pad to 30 columns
    fields += [""] * (30 - len(fields))
    csv_bytes = (";".join(f'"{f}"' for f in fields) + "\n").encode("latin-1")
    row = next(parse_csv_bytes(csv_bytes, ESTABELECIMENTOS_COLS))
    # Date parsing failure → None (covers parser.py lines 155-156).
    assert row["data_situacao_cadastral"] is None


def test_parse_zip_routes_ref_files_to_codigo_descricao(tmp_path: Path) -> None:
    """REF_* file inside zip should be parsed with 2-column [codigo, descricao]."""
    zip_path = tmp_path / "F.K03200$Z.D40510.CNAECSV.zip"
    inner_name = "F.K03200$Z.D40510.CNAECSV"
    csv_content = '"4731800";"Comercio varejista de combustiveis"\n"4732600";"Comercio outro"\n'
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(inner_name, csv_content.encode("latin-1"))

    rows = list(parse_zip(zip_path))
    assert len(rows) == 2
    assert rows[0] == {"codigo": "4731800", "descricao": "Comercio varejista de combustiveis"}
    assert rows[1] == {"codigo": "4732600", "descricao": "Comercio outro"}


def test_parse_zip_skips_empty_lines_inside(tmp_path: Path) -> None:
    """Empty lines inside a zip CSV should be skipped (parser.py line 225)."""
    zip_path = tmp_path / "K3241.K03200Y0.D40510.EMPRECSV.zip"
    inner_name = "K3241.K03200Y0.D40510.EMPRECSV"
    csv_content = (
        '"12345678";"X";"2135";"05";"0";"01";""\n'
        "\n\n"  # blank rows in the middle
        '"99999999";"Y";"2135";"05";"0";"01";""\n'
    )
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(inner_name, csv_content.encode("latin-1"))

    rows = list(parse_zip(zip_path))
    assert len(rows) == 2
    assert rows[0]["cnpj_basico"] == "12345678"
    assert rows[1]["cnpj_basico"] == "99999999"


def test_parse_zip_skips_unknown_when_no_override(tmp_path: Path) -> None:
    """Unknown inner files and no columns_override → continue (parser.py line 216)."""
    zip_path = tmp_path / "mixed-unknown.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", b"not a csv")  # UNKNOWN type, no columns
        zf.writestr("LICENSE", b"some license blob")  # UNKNOWN
    rows = list(parse_zip(zip_path))
    assert rows == []


# ------------ Zip wrapper ------------


def test_parse_zip_routes_to_correct_columns(tmp_path: Path) -> None:
    """A zip containing an EMPRESAS file should be parsed using EMPRESAS_COLS."""
    zip_path = tmp_path / "K3241.K03200Y0.D40510.EMPRECSV.zip"
    inner_name = "K3241.K03200Y0.D40510.EMPRECSV"
    csv_content = '"12345678";"PETROBRAS";"2011";"05";"100,00";"05";""\n'
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(inner_name, csv_content.encode("latin-1"))

    rows = list(parse_zip(zip_path))
    assert len(rows) == 1
    assert rows[0]["razao_social"] == "PETROBRAS"
    assert rows[0]["capital_social"] == Decimal("100.00")


def test_parse_zip_skips_unknown_files(tmp_path: Path) -> None:
    zip_path = tmp_path / "mixed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", b"not a csv")
        zf.writestr(
            "K3241.K03200Y0.D40510.EMPRECSV",
            '"99999999";"X";"2135";"05";"0";"01";""\n'.encode("latin-1"),
        )
    rows = list(parse_zip(zip_path))
    assert len(rows) == 1
    assert rows[0]["cnpj_basico"] == "99999999"
