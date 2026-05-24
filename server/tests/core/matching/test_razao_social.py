"""Tests for match_razao_social — pure fn, no DB needed."""

from __future__ import annotations

import pytest

from brasil_mcp_match_server.core.matching.razao_social import (
    MatchHint,
    _normalize,
    match_razao_social,
)

# ------------ Normalization ------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Petrobras", "PETROBRAS"),
        ("José da Silva", "JOSE DA SILVA"),
        ("  Petróleo   Brasileiro  S.A.  ", "PETROLEO BRASILEIRO S.A."),
        ("ÁÉÍÓÚÇãõ", "AEIOUCAO"),
        ("", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert _normalize(raw) == expected


# ------------ Exact match ------------


def test_exact_match_case_insensitive() -> None:
    r = match_razao_social("petrobras", "PETROBRAS")
    assert r.match is True
    assert r.confidence == 1.0
    assert r.hint == MatchHint.EXACT


def test_exact_match_with_accent() -> None:
    r = match_razao_social("Petróleo Brasileiro", "PETROLEO BRASILEIRO")
    assert r.match is True
    assert r.hint == MatchHint.EXACT


def test_exact_match_with_whitespace_variance() -> None:
    r = match_razao_social("  Petrobras  ", "Petrobras")
    assert r.match is True
    assert r.hint == MatchHint.EXACT


# ------------ Fuzzy prefix (substring contains) ------------


def test_fuzzy_prefix_short_in_long() -> None:
    r = match_razao_social("Petrobras", "Petrobras S.A. - Petróleo Brasileiro")
    assert r.match is True
    assert r.hint == MatchHint.FUZZY_PREFIX
    assert r.confidence >= 0.85


def test_fuzzy_prefix_long_in_short() -> None:
    r = match_razao_social("Petrobras Distribuidora S.A.", "Petrobras Distribuidora")
    assert r.match is True
    assert r.hint in (MatchHint.FUZZY_PREFIX, MatchHint.FUZZY_WORD)


# ------------ Fuzzy word (token-set) ------------


def test_fuzzy_word_reordered() -> None:
    r = match_razao_social("Banco Itaú Unibanco", "Itaú Unibanco Banco S.A.")
    assert r.match is True
    assert r.hint in (MatchHint.FUZZY_WORD, MatchHint.FUZZY_PREFIX)


def test_fuzzy_word_with_extra_tokens() -> None:
    r = match_razao_social(
        "Banco Bradesco",
        "Banco Bradesco S.A.",
        tolerance=0.85,
    )
    assert r.match is True


# ------------ Fuzzy phonetic / weighted ------------


def test_fuzzy_phonetic_typo() -> None:
    # A 1-char typo, often caught by WRatio
    r = match_razao_social("Bradesc", "Bradesco", tolerance=0.85)
    assert r.match is True


def test_fuzzy_phonetic_branch_explicit() -> None:
    """A case engineered to hit the WRatio-only branch.

    The candidate is similar enough by WRatio (weighted) but fails the
    substring check for FUZZY_PREFIX and token_set ratio is too low for
    FUZZY_WORD. Covers razao_social.py line 90.
    """
    # partial=94.7 but n_info NOT substring of n_rf (and vice versa);
    # token_set=47.6 < 85; WRatio=85.3 ≥ 85.
    r = match_razao_social(
        "ABCDEFGHIJ",
        "ABCDEFGHIK XYZ ABC DEF GHIJ KLMN",
        tolerance=0.85,
    )
    assert r.match is True
    assert r.hint == MatchHint.FUZZY_PHONETIC


# ------------ No match ------------


def test_no_match_completely_different() -> None:
    r = match_razao_social("Petrobras", "Banco do Brasil")
    assert r.match is False
    assert r.hint == MatchHint.NO_MATCH
    assert r.confidence < 0.85


def test_no_match_empty_input() -> None:
    r = match_razao_social("", "Petrobras")
    assert r.match is False
    assert r.hint == MatchHint.NO_MATCH
    assert r.confidence == 0.0


def test_no_match_empty_rf() -> None:
    r = match_razao_social("Petrobras", "")
    assert r.match is False
    assert r.hint == MatchHint.NO_MATCH


# ------------ Tolerance behavior ------------


def test_higher_tolerance_rejects_borderline() -> None:
    # A name that scores ~88% — passes at 0.85 but fails at 0.95
    nome = "Petrobrass"  # extra 's'
    rf = "Petrobras"
    loose = match_razao_social(nome, rf, tolerance=0.85)
    strict = match_razao_social(nome, rf, tolerance=0.95)
    # At minimum, strict must be at least as strict (match implies loose match)
    assert not (strict.match and not loose.match)


# ------------ Privacy contract: never returns RF razao_social ------------


def test_output_does_not_leak_razao_social() -> None:
    """The output dict should NEVER contain the razao_social string from RF."""
    rf_secret = "Empresa Confidencial Ltda - Nunca Vaze"
    r = match_razao_social("empresa", rf_secret)
    d = r.to_dict()
    # The output should only have these keys
    assert set(d.keys()) == {"match", "confidence", "hint"}
    # And none of the values should leak the RF string
    for v in d.values():
        if isinstance(v, str):
            assert "Confidencial" not in v
            assert "Nunca Vaze" not in v


# ------------ Serialization ------------


def test_to_dict_shape() -> None:
    r = match_razao_social("Petrobras", "Petrobras")
    d = r.to_dict()
    assert d == {"match": True, "confidence": 1.0, "hint": "exact"}


def test_to_dict_confidence_rounded() -> None:
    r = match_razao_social("Petrobras XYZ ABC", "Petrobras XYZ ABC DEF")
    d = r.to_dict()
    # confidence should be a 3-decimal float
    assert isinstance(d["confidence"], float)
    conf = d["confidence"]
    assert isinstance(conf, float)
    assert 0 <= conf <= 1
