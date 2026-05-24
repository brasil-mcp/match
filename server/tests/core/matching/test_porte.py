"""Tests for check_porte_empresa."""

from __future__ import annotations

import pytest

from brasil_mcp_match_server.core.matching.porte import (
    PorteEmpresa,
    check_porte_empresa,
)


@pytest.mark.parametrize(
    "codigo,expected",
    [
        ("01", PorteEmpresa.MEI),
        ("03", PorteEmpresa.ME),
        ("05", PorteEmpresa.EPP),
        ("00", PorteEmpresa.DEMAIS),
        (1, PorteEmpresa.MEI),
        (3, PorteEmpresa.ME),
        (5, PorteEmpresa.EPP),
        (None, PorteEmpresa.DESCONHECIDO),
        ("", PorteEmpresa.DESCONHECIDO),
        ("abc", PorteEmpresa.DESCONHECIDO),
    ],
)
def test_porte_code_mapping(codigo: object, expected: PorteEmpresa) -> None:
    r = check_porte_empresa(codigo)  # type: ignore[arg-type]
    assert r.porte == expected


def test_simples_flag() -> None:
    r = check_porte_empresa("03", opcao_simples="S")
    assert r.is_simples_nacional is True
    r2 = check_porte_empresa("03", opcao_simples="N")
    assert r2.is_simples_nacional is False
    r3 = check_porte_empresa("03")
    assert r3.is_simples_nacional is False


def test_mei_flag_overrides_porte() -> None:
    """Se opcao_mei = 'S', porte deve ser MEI mesmo que codigo_porte_rf diga outro."""
    r = check_porte_empresa("03", opcao_mei="S")
    assert r.is_mei is True
    assert r.porte == PorteEmpresa.MEI


def test_mei_lowercase_accepted() -> None:
    r = check_porte_empresa("01", opcao_simples="s", opcao_mei="s")
    assert r.is_simples_nacional is True
    assert r.is_mei is True


def test_to_dict_shape() -> None:
    r = check_porte_empresa("05", opcao_simples="S", opcao_mei="N")
    d = r.to_dict()
    assert d == {"porte": "EPP", "is_simples_nacional": True, "is_mei": False}


def test_output_only_has_documented_fields() -> None:
    r = check_porte_empresa("05", opcao_simples="S", opcao_mei="N")
    d = r.to_dict()
    assert set(d.keys()) == {"porte", "is_simples_nacional", "is_mei"}
