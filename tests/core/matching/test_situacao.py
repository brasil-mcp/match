"""Tests for check_situacao_cadastral."""

from __future__ import annotations

from datetime import date

import pytest

from brasil_mcp_match.core.matching.situacao import (
    SituacaoCadastral,
    check_situacao_cadastral,
)


@pytest.mark.parametrize(
    "codigo,expected",
    [
        (1, SituacaoCadastral.NULA),
        (2, SituacaoCadastral.ATIVA),
        (3, SituacaoCadastral.SUSPENSA),
        (4, SituacaoCadastral.INAPTA),
        (8, SituacaoCadastral.BAIXADA),
        ("2", SituacaoCadastral.ATIVA),  # string code from CSV
        ("08", SituacaoCadastral.BAIXADA),
        (0, SituacaoCadastral.DESCONHECIDA),
        (99, SituacaoCadastral.DESCONHECIDA),
        (None, SituacaoCadastral.DESCONHECIDA),
        ("", SituacaoCadastral.DESCONHECIDA),
        ("abc", SituacaoCadastral.DESCONHECIDA),
    ],
)
def test_check_situacao_returns_expected_enum(codigo: object, expected: SituacaoCadastral) -> None:
    r = check_situacao_cadastral(codigo)  # type: ignore[arg-type]
    assert r.situacao == expected


def test_since_date_propagated() -> None:
    r = check_situacao_cadastral(2, since=date(2024, 1, 15))
    assert r.since == date(2024, 1, 15)
    assert r.to_dict()["since"] == "2024-01-15"


def test_since_none_in_output() -> None:
    r = check_situacao_cadastral(2)
    assert r.to_dict()["since"] is None


def test_to_dict_shape() -> None:
    r = check_situacao_cadastral(2, since=date(2024, 1, 15))
    d = r.to_dict()
    assert d == {"situacao": "ativa", "since": "2024-01-15"}


def test_output_does_not_leak_other_fields() -> None:
    """The output should NEVER expose razao_social, capital, or other RF fields."""
    r = check_situacao_cadastral(2, since=date(2024, 1, 15))
    d = r.to_dict()
    assert set(d.keys()) == {"situacao", "since"}
