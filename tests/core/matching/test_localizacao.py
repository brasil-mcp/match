"""Tests for match_uf, match_municipio, match_cep."""

from __future__ import annotations

import pytest

from brasil_mcp_match.core.matching.localizacao import (
    match_cep,
    match_municipio,
    match_uf,
)

# ------------ match_uf ------------


@pytest.mark.parametrize(
    "informed,rf,expected",
    [
        ("SP", "SP", True),
        ("sp", "SP", True),
        ("SP", "sp", True),
        ("  SP  ", "SP", True),
        ("RJ", "SP", False),
        ("", "SP", False),
        ("SP", "", False),
        ("SP", None, False),
    ],
)
def test_match_uf(informed: str, rf: str | None, expected: bool) -> None:
    r = match_uf(informed, rf)
    assert r.match is expected


def test_match_uf_output_shape() -> None:
    assert match_uf("SP", "SP").to_dict() == {"match": True}


# ------------ match_municipio ------------


@pytest.mark.parametrize(
    "informed,rf,expected",
    [
        ("São Paulo", "SAO PAULO", True),
        ("São Paulo", "São Paulo", True),
        ("são paulo", "SAO PAULO", True),
        ("  São   Paulo  ", "SAO PAULO", True),
        ("Sao Paulo", "Rio de Janeiro", False),
        ("", "SAO PAULO", False),
        ("SAO PAULO", "", False),
        ("SAO PAULO", None, False),
    ],
)
def test_match_municipio(informed: str, rf: str | None, expected: bool) -> None:
    r = match_municipio(informed, rf)
    assert r.match is expected


# ------------ match_cep ------------


@pytest.mark.parametrize(
    "informed,rf,expected",
    [
        ("01310200", "01310200", True),
        ("01310-200", "01310200", True),
        ("01310-200", "01310-200", True),
        ("01.310-200", "01310200", True),
        ("01310200", "01311000", False),
        ("123", "01310200", False),  # too short
        ("01310200", "123", False),
        ("", "01310200", False),
        ("01310200", None, False),
    ],
)
def test_match_cep(informed: str, rf: str | None, expected: bool) -> None:
    r = match_cep(informed, rf)
    assert r.match is expected


# ------------ Privacy: outputs only contain {match} ------------


def test_localizacao_outputs_minimal() -> None:
    """All localization match outputs should have ONLY the 'match' boolean."""
    assert set(match_uf("SP", "SP").to_dict().keys()) == {"match"}
    assert set(match_municipio("X", "X").to_dict().keys()) == {"match"}
    assert set(match_cep("01310200", "01310200").to_dict().keys()) == {"match"}
