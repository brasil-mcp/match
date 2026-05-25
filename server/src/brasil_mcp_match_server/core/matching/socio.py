"""Sócio match/check — privacy-preserving (match, don't reveal).

Five pure functions consumed by the REST routes under ``/v1/socio/``:

- ``match_nome_socio``  — fuzzy name match across ALL sócios; reveals only a
  boolean + hint + confidence (NEVER which sócio matched, never the name).
- ``match_cpf_socio``   — boolean check against the 6-digit window the RF
  publishes between ``***`` and ``**`` of ``cnpj_cpf_socio``.
- ``match_cnpj_socio``  — boolean check for a candidate 14-digit CNPJ among
  the parent's PJ sócios.
- ``check_qualificacao``— booleano + count of sócios with a given qualificacao.
- ``count_socios``      — aggregate counts by ``identificador_socio``.

The fuzzy chain mirrors :mod:`brasil_mcp_match_server.core.matching.razao_social`:
exact → fuzzy_prefix → fuzzy_word → fuzzy_phonetic.

LGPD: outputs are booleans/integers/enum-hints. No names, CPFs, qualificacao
descriptions or any other PII appears in the dataclass fields exposed by
``to_dict``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from rapidfuzz import fuzz

# ----------------------------------------------------------------------------
# Constants & masks
# ----------------------------------------------------------------------------

# RF publishes PF CPFs as ``***DDDDDD**`` (3 leading stars, 6 visible digits,
# 2 trailing stars). The pattern is anchored so weirdly-shaped rows fall
# through to the "no match" branch rather than crashing or matching by accident.
_CPF_MASK_RE = re.compile(r"^\*{3}(\d{6})\*{2}$")

# Digit extractor for user-provided CPFs (strip punctuation, spaces, dots, etc).
_NON_DIGIT_RE = re.compile(r"\D")

# PF/PJ/estrangeiro identifiers as used by the RF.
_IDENT_PF = 1
_IDENT_PJ = 2
_IDENT_ESTRANGEIRO = 3


# ----------------------------------------------------------------------------
# Hints / result dataclasses
# ----------------------------------------------------------------------------


class SocioMatchHint(StrEnum):
    """Same vocabulary as razao_social matches — kept verbatim so clients
    can reuse hint-handling logic."""

    EXACT = "exact"
    FUZZY_PREFIX = "fuzzy_prefix"
    FUZZY_WORD = "fuzzy_word"
    FUZZY_PHONETIC = "fuzzy_phonetic"
    NO_MATCH = "no_match"


@dataclass(frozen=True, slots=True)
class SocioNomeMatchResult:
    """Output of ``match_nome_socio``.

    Carries ONLY booleans, confidence in [0, 1], and a hint enum. We do NOT
    carry the matched sócio's name or any identifying field — the privacy
    contract is that callers can confirm membership but cannot enumerate the
    sócio table.
    """

    match: bool
    confidence: float  # 0.0 to 1.0 — best score across all sócios
    hint: SocioMatchHint

    def to_dict(self) -> dict[str, object]:
        return {
            "match": self.match,
            "confidence": round(self.confidence, 3),
            "hint": str(self.hint),
        }


@dataclass(frozen=True, slots=True)
class QualificacaoCheckResult:
    """Output of ``check_qualificacao``.

    ``exists`` is True iff at least one sócio carries the requested codigo.
    ``count`` is the number of sócios with that codigo (never names).
    """

    exists: bool
    count: int

    def to_dict(self) -> dict[str, object]:
        return {"exists": self.exists, "count": self.count}


@dataclass(frozen=True, slots=True)
class SocioCountResult:
    """Output of ``count_socios``.

    Aggregates only — never lists names, CPFs, or qualificações.
    """

    total: int
    pf: int
    pj: int
    estrangeiro: int

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "pf": self.pf,
            "pj": self.pj,
            "estrangeiro": self.estrangeiro,
        }


# ----------------------------------------------------------------------------
# Helpers (private)
# ----------------------------------------------------------------------------


def _normalize(s: str) -> str:
    """Strip accents, uppercase, collapse whitespace (same rules as razao_social)."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(no_accents.upper().split())


_HINT_RANK: dict[SocioMatchHint, int] = {
    SocioMatchHint.EXACT: 4,
    SocioMatchHint.FUZZY_PREFIX: 3,
    SocioMatchHint.FUZZY_WORD: 2,
    SocioMatchHint.FUZZY_PHONETIC: 1,
    SocioMatchHint.NO_MATCH: 0,
}


def _score_pair(
    n_info: str, n_socio: str, threshold: float
) -> tuple[bool, float, SocioMatchHint]:
    """Run the exact → prefix → word → phonetic chain for a single sócio name.

    Returns ``(match, confidence_0_to_1, hint)``. Identical semantics to the
    razao_social matcher — copy-paste with rename so the two evolve independently.
    """
    if not n_info or not n_socio:
        return (False, 0.0, SocioMatchHint.NO_MATCH)

    if n_info == n_socio:
        return (True, 1.0, SocioMatchHint.EXACT)

    partial = fuzz.partial_ratio(n_info, n_socio)
    if partial >= threshold and (n_info in n_socio or n_socio in n_info):
        return (True, partial / 100, SocioMatchHint.FUZZY_PREFIX)

    token_set = fuzz.token_set_ratio(n_info, n_socio)
    if token_set >= threshold:
        return (True, token_set / 100, SocioMatchHint.FUZZY_WORD)

    weighted = fuzz.WRatio(n_info, n_socio)
    if weighted >= threshold:
        return (True, weighted / 100, SocioMatchHint.FUZZY_PHONETIC)

    best = max(partial, token_set, weighted)
    return (False, best / 100, SocioMatchHint.NO_MATCH)


# ----------------------------------------------------------------------------
# Public matchers
# ----------------------------------------------------------------------------


# Type-erased SocioRecord to avoid a hard import dependency in the matcher
# layer. Repository's ``SocioRecord`` is structurally compatible via attribute
# access (nome_socio, cnpj_cpf_socio, identificador_socio, qualificacao_socio).
# Defined as a runtime-checkable Protocol so duck-typed test doubles and the
# real ``SocioRecord`` dataclass both satisfy the type checker.
class _SocioLike(Protocol):
    @property
    def nome_socio(self) -> str: ...
    @property
    def cnpj_cpf_socio(self) -> str | None: ...
    @property
    def identificador_socio(self) -> int | None: ...
    @property
    def qualificacao_socio(self) -> int | None: ...


def match_nome_socio(
    socios: Iterable[_SocioLike],
    nome_input: str,
    tolerance: float = 0.85,
) -> SocioNomeMatchResult:
    """Best-of-all-sócios fuzzy match.

    Iterates every sócio, scores via the exact→prefix→word→phonetic chain,
    and returns the strongest hit. If multiple sócios tie, the one with the
    highest hint rank wins; ties on hint use highest confidence.

    Returns ``{match: False, hint: NO_MATCH, confidence: best_score}`` if no
    sócio passes ``tolerance``. NEVER returns which sócio matched.
    """
    n_info = _normalize(nome_input)
    threshold = tolerance * 100

    if not n_info:
        return SocioNomeMatchResult(False, 0.0, SocioMatchHint.NO_MATCH)

    best_hint: SocioMatchHint = SocioMatchHint.NO_MATCH
    best_conf: float = 0.0
    any_match = False

    for s in socios:
        nome = s.nome_socio
        if not nome:
            continue
        n_socio = _normalize(nome)
        matched, conf, hint = _score_pair(n_info, n_socio, threshold)
        if matched:
            any_match = True
            # Rank by hint priority first; on tie, by confidence.
            if _HINT_RANK[hint] > _HINT_RANK[best_hint] or (
                _HINT_RANK[hint] == _HINT_RANK[best_hint] and conf > best_conf
            ):
                best_hint = hint
                best_conf = conf
        elif not any_match and conf > best_conf:
            # Track best near-miss for transparency in the no-match case.
            best_conf = conf

    if not any_match:
        return SocioNomeMatchResult(False, best_conf, SocioMatchHint.NO_MATCH)
    return SocioNomeMatchResult(True, best_conf, best_hint)


def _extract_cpf_window(masked: str) -> str | None:
    """Pull the 6 visible digits out of the RF mask ``***DDDDDD**``.

    Returns None when the input doesn't match the canonical mask — defensive
    against pre-fmt rows or PJ sócios that store a full CNPJ in the field.
    """
    if not masked:
        return None
    m = _CPF_MASK_RE.match(masked.strip())
    if m is None:
        return None
    return m.group(1)


def match_cpf_socio(
    socios: Iterable[_SocioLike],
    cpf_input: str,
) -> bool:
    """Verify a CPF belongs to any PF sócio of the parent CNPJ.

    The user supplies a full 11-digit CPF (with or without ``.``/``-``).
    We compare positions 4-9 (the 6 digits the RF leaves visible) against
    the masked column for each PF sócio. Returns True on the first match.

    Raises ``ValueError`` if the CPF doesn't normalize to 11 digits — the
    REST layer translates that to a 422.
    """
    digits = _NON_DIGIT_RE.sub("", cpf_input or "")
    if len(digits) != 11:
        raise ValueError("CPF must normalize to 11 digits")

    # Positions 4-9 in 1-indexed terms == input[3:9] in 0-indexed slice.
    # Guaranteed 6 chars given the len(digits)==11 check above.
    window = digits[3:9]

    for s in socios:
        if s.identificador_socio != _IDENT_PF:
            continue
        masked = s.cnpj_cpf_socio
        if masked is None:
            continue
        socio_window = _extract_cpf_window(masked)
        if socio_window is not None and socio_window == window:
            return True
    return False


def match_cnpj_socio(
    socios: Iterable[_SocioLike],
    cnpj_socio_input: str,
) -> bool:
    """Verify a 14-digit CNPJ is registered as a PJ sócio of the parent CNPJ.

    Caller is expected to have validated/normalized ``cnpj_socio_input`` to
    14 digits already (the REST layer does it). We just compare strings
    against the rows with ``identificador_socio == 2``.
    """
    target = _NON_DIGIT_RE.sub("", cnpj_socio_input or "")
    if len(target) != 14:
        return False
    for s in socios:
        if s.identificador_socio != _IDENT_PJ:
            continue
        stored = (s.cnpj_cpf_socio or "").strip()
        if stored == target:
            return True
    return False


def check_qualificacao(
    socios: Iterable[_SocioLike],
    qualificacao_codigo: int,
) -> QualificacaoCheckResult:
    """Returns ``(exists, count)`` for the requested qualificacao codigo.

    Names of sócios are NEVER exposed — only the aggregate count.
    """
    count = sum(1 for s in socios if s.qualificacao_socio == qualificacao_codigo)
    return QualificacaoCheckResult(exists=count > 0, count=count)


def count_socios(socios: Iterable[_SocioLike]) -> SocioCountResult:
    """Aggregate counts by ``identificador_socio``.

    Unknown/null identificadores are counted in ``total`` but NOT in any of
    pf/pj/estrangeiro — the three known buckets must always sum ≤ total.
    """
    pf = pj = estr = total = 0
    for s in socios:
        total += 1
        ident = s.identificador_socio
        if ident == _IDENT_PF:
            pf += 1
        elif ident == _IDENT_PJ:
            pj += 1
        elif ident == _IDENT_ESTRANGEIRO:
            estr += 1
    return SocioCountResult(total=total, pf=pf, pj=pj, estrangeiro=estr)
